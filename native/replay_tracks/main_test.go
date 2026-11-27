package main

import (
	"encoding/binary"
	"math"
	"os"
	"path/filepath"
	"testing"
)

func TestPieceSlotUniqueAcrossSerials(t *testing.T) {
	a := pieceSlot(3315, 673)
	b := pieceSlot(3315, 674)
	c := pieceSlot(3316, 673)
	if a == b || a == c || b == c {
		t.Fatalf("expected distinct slots, got a=%x b=%x c=%x", a, b, c)
	}
}

func TestCellsOfInterpolationAndDelay(t *testing.T) {
	// Move from x=0 at tick 61 to x=640 at tick 125 (1.0 s).
	// With drawDelayTicks=3, drawn times are t0=64/64=1.0s and t1=128/64=2.0s.
	// Grid cells in (1.0, 2.0] are g=16..30 (15 cells).
	entities := map[uint32][]sample{
		pieceSlot(3064, 338): {
			{tick: 61, serial: 338, alive: true, pos: [3]float32{0, 10, 650}},
			{tick: 125, serial: 338, alive: true, pos: [3]float32{640, 10, 650}},
			// Dead end sample must not produce cells after tick 125.
			{tick: 189, serial: 338, alive: false, pos: [3]float32{900, 10, 650}},
		},
	}
	cells := cellsOf(entities)
	if len(cells) != 15 {
		t.Fatalf("expected 15 cells, got %d", len(cells))
	}
	if cells[0].grid != 16 || cells[len(cells)-1].grid != 30 {
		t.Fatalf("unexpected grid span: %d..%d", cells[0].grid, cells[len(cells)-1].grid)
	}
	last := cells[len(cells)-1]
	if math.Abs(float64(last.pos[0]-640.0)) > 1e-3 || last.pos[1] != 10 || last.pos[2] != 650 {
		t.Fatalf("unexpected last cell pos: %v", last.pos)
	}
}

func TestWriteDLYTRK03Format(t *testing.T) {
	dir := t.TempDir()
	out := filepath.Join(dir, "sample.tracks")
	heroCells := []cell{
		{grid: 15, slot: 7, pos: [3]float32{1.5, -2.5, 256.0}},
		{grid: 16, slot: 7, pos: [3]float32{11.5, -2.5, 256.0}},
	}
	pieceCells := []cell{
		{grid: 15, slot: pieceSlot(3064, 338), pos: [3]float32{168.0, 75.0, 654.0}},
	}
	if err := write(out, heroCells, pieceCells, 12.5); err != nil {
		t.Fatalf("write failed: %v", err)
	}
	data, err := os.ReadFile(out)
	if err != nil {
		t.Fatalf("read failed: %v", err)
	}
	wantLen := 8 + 4 + 4 + 16 + len(heroCells)*20 + 4 + len(pieceCells)*20
	if len(data) != wantLen {
		t.Fatalf("unexpected size: got %d, want %d", len(data), wantLen)
	}
	if string(data[:8]) != "DLYTRK03" {
		t.Fatalf("unexpected magic: %q", string(data[:8]))
	}
	le := binary.LittleEndian
	if got := le.Uint32(data[8:12]); got != uint32(len(heroCells)) {
		t.Fatalf("hero count=%d, want %d", got, len(heroCells))
	}
	if got := le.Uint32(data[12:16]); got != 1 {
		t.Fatalf("range count=%d, want 1", got)
	}
	if got := math.Float64frombits(le.Uint64(data[24:32])); got != 12.5 {
		t.Fatalf("seenEnd=%v, want 12.5", got)
	}
	pieceOff := 32 + len(heroCells)*20
	if got := le.Uint32(data[pieceOff : pieceOff+4]); got != uint32(len(pieceCells)) {
		t.Fatalf("piece count=%d, want %d", got, len(pieceCells))
	}
	p0 := pieceOff + 4
	if got := int32(le.Uint32(data[p0 : p0+4])); got != 15 {
		t.Fatalf("piece grid=%d, want 15", got)
	}
	if got := le.Uint32(data[p0+4 : p0+8]); got != pieceSlot(3064, 338) {
		t.Fatalf("piece slot=%x, want %x", got, pieceSlot(3064, 338))
	}
	if got := math.Float32frombits(le.Uint32(data[p0+8 : p0+12])); got != 168.0 {
		t.Fatalf("piece x=%v, want 168.0", got)
	}
}
