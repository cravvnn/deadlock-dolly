// DollyReplayTracks reads every hero's and Solomon chess piece's position from a completed
// Deadlock replay with s2replay (github.com/paralin/s2replay, MIT) and writes them as track
// cells: the same "DLYTRK03" file the game keeps for a replay while it plays
// (dolly_weather_state.hpp, trails::Store). The game merges these into the replay's store,
// so tracks are ready before the shot has ever been played. The replay itself is only read.
//
//	DollyReplayTracks <replay.dem> <out.tracks>
//
// Positions are those drawn on screen: the game draws networked entities three ticks behind
// the position the replay records (measured against cells the game kept live: a median
// 0.04 u apart), and each 1/15 s cell is the line between the two recorded ticks around it,
// as the game's own interpolation draws it.
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
	tickRate        = 64.0
	step            = 1.0 / 15.0
	drawDelayTicks  = 3.0
	heroClass       = "CCitadelPlayerPawn"
	chessPieceClass = "CNPC_ChessPiece"
	fileMagic       = "DLYTRK03"
	maxCells        = 2 * 1024 * 1024 // the store's own limit
	maxReplayTicks  = 64 * 60 * 60 * 3
	maxEntityIndex  = 8192 // Deadlock entities sit well below this bound
	pieceScanStride = 8    // discover newly spawned chess pieces every 8 ticks (1/8 s)
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
	heroes, pieces, last, err := read(os.Args[1])
	if err != nil {
		fail(err)
	}
	heroCells := cellsOf(heroes)
	if len(heroCells) == 0 {
		fail(errors.New("this replay has no hero positions"))
	}
	pieceCells := cellsOf(pieces)
	if len(heroCells)+len(pieceCells) > maxCells {
		fail(errors.New("this replay has more track positions than a track store holds"))
	}
	seenEnd := (float64(last) + drawDelayTicks) / tickRate
	if err := write(os.Args[2], heroCells, pieceCells, seenEnd); err != nil {
		fail(err)
	}
	fmt.Printf("{\"abi\":1,\"cells\":%d,\"heroes\":%d,\"piece_cells\":%d,\"pieces\":%d,\"seen_end\":%.3f}\n",
		len(heroCells), len(heroes), len(pieceCells), len(pieces), seenEnd)
}

func fail(err error) {
	fmt.Fprintln(os.Stderr, err.Error())
	os.Exit(1)
}

func validPos(x, y, z float32) bool {
	fx, fy, fz := float64(x), float64(y), float64(z)
	return !math.IsNaN(fx+fy+fz) && !math.IsInf(fx+fy+fz, 0) &&
		math.Abs(fx) <= 1e6 && math.Abs(fy) <= 1e6 && math.Abs(fz) <= 1e6
}

// pieceSlot packs a Source 2 entity index (14 bits) and serial (17 bits) into a unique
// per-incarnation uint32 so a reused entity index never joins two different chess pieces.
func pieceSlot(entity, serial int32) uint32 {
	return (uint32(serial&0x1ffff) << 14) | uint32(entity&0x3fff)
}

func appendSample(bySlot map[uint32][]sample, slot uint32, next sample) {
	list := bySlot[slot]
	if n := len(list); n > 0 && list[n-1].tick == next.tick {
		list[n-1] = next // the last update of a tick is that tick's position
	} else {
		list = append(list, next)
	}
	bySlot[slot] = list
}

// read returns every hero pawn's samples by entity index, every Solomon chess piece's
// samples by packed entity handle, and the replay's last tick.
func read(path string) (map[uint32][]sample, map[uint32][]sample, uint32, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, nil, 0, err
	}
	defer f.Close()
	p, err := s2replay.NewParserFromReader(context.Background(), bufio.NewReaderSize(f, 1<<20))
	if err != nil {
		return nil, nil, 0, err
	}
	heroes := map[uint32][]sample{}
	pieces := map[uint32][]sample{}
	activePieces := map[int32]struct{}{}
	var last uint32
	var lastPieceTick uint32
	var lastScanTick uint32
	havePieceTick := false
	scanned := false

	samplePieces := func(tick uint32) {
		if !scanned || tick >= lastScanTick+pieceScanStride {
			scanned = true
			lastScanTick = tick
			for idx := int32(0); idx < maxEntityIndex; idx++ {
				e := p.FindEntity(idx)
				if e != nil && e.ClassName() == chessPieceClass {
					activePieces[idx] = struct{}{}
				}
			}
		}
		for idx := range activePieces {
			e := p.FindEntity(idx)
			if e == nil || e.ClassName() != chessPieceClass {
				delete(activePieces, idx)
				continue
			}
			ps, ok := p.SampleEntity(e)
			if !ok || ps.Entity < 0 || ps.Tick > maxReplayTicks ||
				!validPos(ps.PositionX, ps.PositionY, ps.PositionZ) {
				continue
			}
			slot := pieceSlot(ps.Entity, ps.EntitySerial)
			appendSample(pieces, slot, sample{
				tick:   ps.Tick,
				serial: ps.EntitySerial,
				alive:  ps.LifeState == 0,
				pos:    [3]float32{ps.PositionX, ps.PositionY, ps.PositionZ},
			})
		}
	}

	for {
		s, err := p.NextEntitySample()
		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil {
			return nil, nil, 0, fmt.Errorf("could not read the replay's heroes: %w", err)
		}
		if s.Tick > last {
			last = s.Tick
		}
		if !havePieceTick || s.Tick != lastPieceTick {
			havePieceTick = true
			lastPieceTick = s.Tick
			samplePieces(s.Tick)
		}
		if s.ClassName != heroClass || s.Entity < 0 || s.Tick > maxReplayTicks {
			continue
		}
		if !validPos(s.PositionX, s.PositionY, s.PositionZ) {
			continue
		}
		slot := uint32(s.Entity)
		appendSample(heroes, slot, sample{
			tick:   s.Tick,
			serial: s.EntitySerial,
			alive:  s.LifeState == 0,
			pos:    [3]float32{s.PositionX, s.PositionY, s.PositionZ},
		})
	}
	return heroes, pieces, last, nil
}

// cellsOf puts each living entity on the 1/15 s grid of drawn replay time: a cell at grid g
// lies on the line between the two recorded ticks around it (time (tick + delay) / 64).
func cellsOf(entities map[uint32][]sample) []cell {
	slots := make([]uint32, 0, len(entities))
	for slot := range entities {
		slots = append(slots, slot)
	}
	sort.Slice(slots, func(i, j int) bool { return slots[i] < slots[j] })
	var out []cell
	for _, slot := range slots {
		list := entities[slot]
		sort.SliceStable(list, func(i, j int) bool { return list[i].tick < list[j].tick })
		for i := 1; i < len(list); i++ {
			a, b := list[i-1], list[i]
			// A new entity in the slot, or one not alive at both ends, leaves no cells.
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

// write saves the cells as a track store: "DLYTRK03", hero cell count, one seen range
// [0, end], the hero cells (grid i32, slot u32, x y z f32), piece cell count, then the
// piece cells, through a temporary file.
func write(path string, heroCells []cell, pieceCells []cell, seenEnd float64) error {
	partial := path + ".partial"
	f, err := os.Create(partial)
	if err != nil {
		return err
	}
	w := bufio.NewWriterSize(f, 1<<20)
	le := binary.LittleEndian
	buf := make([]byte, 0, 32)
	buf = append(buf, fileMagic...)
	buf = le.AppendUint32(buf, uint32(len(heroCells)))
	buf = le.AppendUint32(buf, 1)
	buf = le.AppendUint64(buf, math.Float64bits(0))
	buf = le.AppendUint64(buf, math.Float64bits(seenEnd))
	w.Write(buf)
	writeCells := func(cells []cell) {
		for _, c := range cells {
			buf = buf[:0]
			buf = le.AppendUint32(buf, uint32(c.grid))
			buf = le.AppendUint32(buf, c.slot)
			for k := 0; k < 3; k++ {
				buf = le.AppendUint32(buf, math.Float32bits(c.pos[k]))
			}
			w.Write(buf)
		}
	}
	writeCells(heroCells)
	buf = buf[:0]
	buf = le.AppendUint32(buf, uint32(len(pieceCells)))
	w.Write(buf)
	writeCells(pieceCells)
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
