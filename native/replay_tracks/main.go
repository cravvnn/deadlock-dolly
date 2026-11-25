// DollyReplayTracks reads every hero's position from a completed Deadlock replay with
// s2replay (github.com/paralin/s2replay, MIT) and writes them as hero track cells: the same
// "DLYTRK02" file the game keeps for a replay while it plays (dolly_weather_state.hpp,
// trails::Store). The game merges these into the replay's store, so tracks are ready before
// the shot has ever been played. The replay itself is only read.
//
//	DollyReplayTracks <replay.dem> <out.tracks>
//
// Positions are those drawn on screen: the game draws a hero three ticks behind the position
// the replay records (measured against cells the game kept live: a median 0.04 u apart), and
// each 1/15 s cell is the line between the two recorded ticks around it, as the game's own
// interpolation draws it.
package main

import (
	"bufio"
	"context"
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"math"
	"os"
	"sort"

	"github.com/paralin/s2replay"
)

const (
	tickRate       = 64.0
	step           = 1.0 / 15.0
	drawDelayTicks = 3.0
	heroClass      = "CCitadelPlayerPawn"
	maxCells       = 2 * 1024 * 1024 // the store's own limit
	maxReplayTicks = 64 * 60 * 60 * 3
)

type sample struct {
	tick   uint32
	serial int32
	alive  bool
	pos    [3]float32
}

type cell struct {
	grid int32
	slot uint32
	pos  [3]float32
}

func main() {
	if len(os.Args) != 3 {
		fail(errors.New("usage: DollyReplayTracks <replay.dem> <out.tracks>"))
	}
	heroes, last, err := read(os.Args[1])
	if err != nil {
		fail(err)
	}
	cells := cellsOf(heroes)
	if len(cells) == 0 {
		fail(errors.New("this replay has no hero positions"))
	}
	if len(cells) > maxCells {
		fail(errors.New("this replay has more hero positions than a track store holds"))
	}
	seenEnd := (float64(last) + drawDelayTicks) / tickRate
	if err := write(os.Args[2], cells, seenEnd); err != nil {
		fail(err)
	}
	fmt.Printf("{\"abi\":1,\"cells\":%d,\"heroes\":%d,\"seen_end\":%.3f}\n", len(cells), len(heroes), seenEnd)
}

func fail(err error) {
	fmt.Fprintln(os.Stderr, err.Error())
	os.Exit(1)
}

// read returns every hero pawn's samples by entity index, and the replay's last tick.
func read(path string) (map[uint32][]sample, uint32, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, 0, err
	}
	defer f.Close()
	p, err := s2replay.NewParserFromReader(context.Background(), bufio.NewReaderSize(f, 1<<20))
	if err != nil {
		return nil, 0, err
	}
	heroes := map[uint32][]sample{}
	var last uint32
	for {
		s, err := p.NextEntitySample()
		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil {
			return nil, 0, fmt.Errorf("could not read the replay's heroes: %w", err)
		}
		if s.Tick > last {
			last = s.Tick
		}
		if s.ClassName != heroClass || s.Entity < 0 || s.Tick > maxReplayTicks {
			continue
		}
		x, y, z := float64(s.PositionX), float64(s.PositionY), float64(s.PositionZ)
		if math.IsNaN(x+y+z) || math.IsInf(x+y+z, 0) || math.Abs(x) > 1e6 || math.Abs(y) > 1e6 ||
			math.Abs(z) > 1e6 {
			continue
		}
		slot := uint32(s.Entity)
		next := sample{tick: s.Tick, serial: s.EntitySerial, alive: s.LifeState == 0,
			pos: [3]float32{s.PositionX, s.PositionY, s.PositionZ}}
		list := heroes[slot]
		if n := len(list); n > 0 && list[n-1].tick == s.Tick {
			list[n-1] = next // the last update of a tick is that tick's position
		} else {
			list = append(list, next)
		}
		heroes[slot] = list
	}
	return heroes, last, nil
}

// cellsOf puts each living hero on the 1/15 s grid of drawn replay time: a cell at grid g lies
// on the line between the two recorded ticks around it (time (tick + delay) / 64).
func cellsOf(heroes map[uint32][]sample) []cell {
	slots := make([]uint32, 0, len(heroes))
	for slot := range heroes {
		slots = append(slots, slot)
	}
	sort.Slice(slots, func(i, j int) bool { return slots[i] < slots[j] })
	var out []cell
	for _, slot := range slots {
		list := heroes[slot]
		sort.SliceStable(list, func(i, j int) bool { return list[i].tick < list[j].tick })
		for i := 1; i < len(list); i++ {
			a, b := list[i-1], list[i]
			// A new pawn in the slot, or a hero who is not alive at both ends, leaves no cells.
			if a.serial != b.serial || !a.alive || !b.alive || b.tick <= a.tick {
				continue
			}
			t0 := (float64(a.tick) + drawDelayTicks) / tickRate
			t1 := (float64(b.tick) + drawDelayTicks) / tickRate
			first := int64(math.Floor(t0/step)) + 1
			last := int64(math.Floor(t1 / step))
			for g := first; g <= last; g++ {
				f := float32((float64(g)*step - t0) / (t1 - t0))
				var pos [3]float32
				for k := 0; k < 3; k++ {
					pos[k] = a.pos[k] + (b.pos[k]-a.pos[k])*f
				}
				out = append(out, cell{grid: int32(g), slot: slot, pos: pos})
			}
		}
	}
	return out
}

// write saves the cells as a track store: "DLYTRK02", cell count, one seen range [0, end],
// then the cells (grid i32, slot u32, x y z f32), through a temporary file.
func write(path string, cells []cell, seenEnd float64) error {
	partial := path + ".partial"
	f, err := os.Create(partial)
	if err != nil {
		return err
	}
	w := bufio.NewWriterSize(f, 1<<20)
	le := binary.LittleEndian
	buf := make([]byte, 0, 32)
	buf = append(buf, "DLYTRK02"...)
	buf = le.AppendUint32(buf, uint32(len(cells)))
	buf = le.AppendUint32(buf, 1)
	buf = le.AppendUint64(buf, math.Float64bits(0))
	buf = le.AppendUint64(buf, math.Float64bits(seenEnd))
	w.Write(buf)
	for _, c := range cells {
		buf = buf[:0]
		buf = le.AppendUint32(buf, uint32(c.grid))
		buf = le.AppendUint32(buf, c.slot)
		for k := 0; k < 3; k++ {
			buf = le.AppendUint32(buf, math.Float32bits(c.pos[k]))
		}
		w.Write(buf)
	}
	if err := w.Flush(); err != nil {
		f.Close()
		os.Remove(partial)
		return err
	}
	if err := f.Close(); err != nil {
		os.Remove(partial)
		return err
	}
	return os.Rename(partial, path)
}
