// recount: everything archr-real.R asserts, counted from the raw fragments file with no ArchR,
// R, htslib or GenomicRanges. Go standard library only; built static, pinned by sha256.
//
//	recount -frag fragments.tsv.gz -cells cells.txt -sizes chrom.sizes -black blacklist.bed.gz \
//	        -genes genes.tsv -peaks peaks.bed -out DIR/
//
// The semantics are ArchR 1.0.3's, read from its source (R/CreateArrow.R, MatrixTiles.R,
// MatrixFeatures.R, GRangesUtils.R), and are the same ones the recipe's planted fixture proved:
//   - a fragment is kept if its chrom is in -sizes, its barcode is a cell and 10 <= end-start0 <= 2000;
//   - its two Tn5 insertions are start0+1 and end (1-based), no further offset for a fragments file;
//   - nMono/nDi/nMulti are trunc(width/147)+1 = 1, 2, >=3;
//   - a feature count (TSS window, TSS flank, promoter, blacklist) is the number of
//     (insertion, feature) overlaps -- an insertion inside two windows counts twice;
//   - tiles are trunc(pos/500)+1 on every chrom but chrM and chrY; a tile any blacklist region
//     touches (start%/%500+1 .. end%/%500+1) is zeroed;
//   - PeakMatrix counts insertions per peak, capped at 4; ReadsInPeaks is the uncapped sum.
//
// Matrices are compared as nnz, sum, sum of squares and sum(x * (w_i * v_j mod P)) mod P with
// w_i = (i*40503 mod 999983)+1 over the row's ArchR idx and v_j = (j*7919 mod 999979)+1 over the
// cell's position in cells.txt -- signac-real's checksum, so a matrix never has to be moved.
package main

import (
	"bufio"
	"bytes"
	"compress/gzip"
	"flag"
	"fmt"
	"io"
	"os"
	"sort"
	"strconv"
	"strings"
)

const P = 1000000007

func die(f string, a ...interface{}) { fmt.Fprintf(os.Stderr, "FAIL: "+f+"\n", a...); os.Exit(1) }

func open(path string) io.Reader {
	f, err := os.Open(path)
	if err != nil {
		die("%v", err)
	}
	br := bufio.NewReaderSize(f, 1<<20)
	if strings.HasSuffix(path, ".gz") {
		z, err := gzip.NewReader(br) // multistream: bgzip blocks are gzip members
		if err != nil {
			die("%s: %v", path, err)
		}
		return bufio.NewReaderSize(z, 1<<22)
	}
	return br
}

func lines(path string, fn func([]string)) {
	sc := bufio.NewScanner(open(path))
	sc.Buffer(make([]byte, 1<<20), 1<<24)
	for sc.Scan() {
		t := sc.Text()
		if t == "" || t[0] == '#' {
			continue
		}
		fn(strings.Split(t, "\t"))
	}
	if err := sc.Err(); err != nil {
		die("%s: %v", path, err)
	}
}

func atoi(s string) int64 {
	v, err := strconv.ParseInt(s, 10, 64)
	if err != nil {
		die("not an integer: %q", s)
	}
	return v
}

// cov: a step function over 1-based positions -- how many intervals of a set cover each base.
type cov struct {
	b []int64 // breakpoints, ascending
	c []int32 // coverage on [b[k], b[k+1])
}

func buildCov(iv [][2]int64) cov {
	ev := map[int64]int32{}
	for _, x := range iv {
		ev[x[0]]++
		ev[x[1]+1]--
	}
	keys := make([]int64, 0, len(ev))
	for k := range ev {
		keys = append(keys, k)
	}
	sort.Slice(keys, func(i, j int) bool { return keys[i] < keys[j] })
	var cv cov
	var run int32
	for _, k := range keys {
		run += ev[k]
		cv.b = append(cv.b, k)
		cv.c = append(cv.c, run)
	}
	return cv
}

func (cv *cov) at(p int64) int64 {
	k := sort.Search(len(cv.b), func(i int) bool { return cv.b[i] > p }) - 1
	if k < 0 {
		return 0
	}
	return int64(cv.c[k])
}

type mstat struct{ nnz, sum, sq, ck int64 }

func (m *mstat) add(i int64, j int, x int64) {
	w := (i*40503)%999983 + 1
	v := (int64(j)*7919)%999979 + 1
	m.nnz++
	m.sum += x
	m.sq += x * x
	m.ck = (m.ck + (x%P)*((w*v)%P)%P) % P
}

type cellc struct {
	recAll, recChrom, kept, mono, di, multi int64
	nW, nF, prom, black                     int64
	tileIns, blkTileIns, exclIns, peakIns   int64
	peakChrKept                             int64 // kept fragments on chroms with a peak: FRIP's denominator / 2
}

func main() {
	frag := flag.String("frag", "", "fragments.tsv.gz")
	cellsF := flag.String("cells", "", "cells.txt, one barcode per line; order fixes v_j")
	sizesF := flag.String("sizes", "", "chrom<TAB>length; only these chroms are read, in this order")
	blackF := flag.String("black", "", "blacklist BED (.gz ok)")
	genesF := flag.String("genes", "", "signac-real's exon table (seqnames start end strand gene_name gene_id gene_biotype tx_id type)")
	peaksF := flag.String("peaks", "", "Cell Ranger peaks.bed")
	out := flag.String("out", "./", "output directory, with trailing /")
	flag.Parse()

	// ---- cells
	cell := map[string]int{}
	var cells []string
	lines(*cellsF, func(f []string) { cells = append(cells, f[0]); cell[f[0]] = len(cells) })
	cc := make([]cellc, len(cells)+1)

	// ---- chrom sizes
	var chroms []string
	clen := map[string]int64{}
	lines(*sizesF, func(f []string) { chroms = append(chroms, f[0]); clen[f[0]] = atoi(f[1]) })
	excluded := map[string]bool{"chrM": true, "chrY": true} // ArchR's excludeChr default
	chromRank := map[string]int{}
	for i, c := range chroms {
		chromRank[c] = i
	}

	// ---- annotation: protein-coding genes and their transcripts' TSSs, from the exon table
	type gene struct {
		chr, strand, id, name string
		s, e                  int64
	}
	type tx struct {
		chr, strand, id string
		s, e            int64
	}
	genes := map[string]*gene{}
	var gorder []string
	txs := map[string]*tx{}
	var torder []string
	hdr := true
	lines(*genesF, func(f []string) {
		if hdr {
			hdr = false
			return
		}
		if f[6] != "protein_coding" {
			return
		}
		if _, ok := clen[f[0]]; !ok {
			return
		}
		s, e := atoi(f[1]), atoi(f[2])
		g := genes[f[5]]
		if g == nil {
			g = &gene{f[0], f[3], f[5], f[4], s, e}
			genes[f[5]] = g
			gorder = append(gorder, f[5])
		}
		if s < g.s {
			g.s = s
		}
		if e > g.e {
			g.e = e
		}
		t := txs[f[7]]
		if t == nil {
			t = &tx{f[0], f[3], f[7], s, e}
			txs[f[7]] = t
			torder = append(torder, f[7])
		}
		if s < t.s {
			t.s = s
		}
		if e > t.e {
			t.e = e
		}
	})
	sort.SliceStable(gorder, func(a, b int) bool {
		x, y := genes[gorder[a]], genes[gorder[b]]
		if x.chr != y.chr {
			return chromRank[x.chr] < chromRank[y.chr]
		}
		if x.s != y.s {
			return x.s < y.s
		}
		return x.id < y.id
	})
	gw, _ := os.Create(*out + "genes_archr.tsv")
	fmt.Fprintln(gw, "chr\tstart\tend\tstrand\tgene_id\tsymbol")
	prom := map[string][][2]int64{}
	for _, id := range gorder {
		g := genes[id]
		name := g.name
		if name == "" {
			name = g.id
		}
		fmt.Fprintf(gw, "%s\t%d\t%d\t%s\t%s\t%s\n", g.chr, g.s, g.e, g.strand, g.id, name)
		if g.strand == "-" { // resize(genes, 1, "start") then extendGR(upstream 2000, downstream 100)
			prom[g.chr] = append(prom[g.chr], [2]int64{g.e - 100, g.e + 2000})
		} else {
			prom[g.chr] = append(prom[g.chr], [2]int64{g.s - 2000, g.s + 100})
		}
	}
	gw.Close()
	tw, _ := os.Create(*out + "tss_archr.tsv")
	fmt.Fprintln(tw, "chr\tpos\tstrand\ttx_id")
	tssPos := map[string]map[int64]bool{}
	for _, id := range torder {
		t := txs[id]
		p := t.s
		if t.strand == "-" {
			p = t.e
		}
		fmt.Fprintf(tw, "%s\t%d\t%s\t%s\n", t.chr, p, t.strand, t.id)
		if tssPos[t.chr] == nil {
			tssPos[t.chr] = map[int64]bool{}
		}
		tssPos[t.chr][p] = true // strand(TSS) <- "*"; unique(TSS)
	}
	tw.Close()
	win, flk := map[string][][2]int64{}, map[string][][2]int64{}
	nTSS := 0
	for c, ps := range tssPos {
		for p := range ps {
			nTSS++
			win[c] = append(win[c], [2]int64{p - 50, p + 50})          // resize(TSS, 101, "center")
			flk[c] = append(flk[c], [2]int64{p + 1901, p + 2000})      // + flank: end + 2000 - 100 + 1 .. end + 2000
			flk[c] = append(flk[c], [2]int64{p - 2000, p - 2000 + 99}) // - flank: start - 2000 .. start - 2000 + 100 - 1
		}
	}

	// ---- blacklist (BED, half-open -> 1-based closed)
	blk := map[string][][2]int64{}
	blkTile := map[string]map[int64]bool{}
	nBlk := 0
	lines(*blackF, func(f []string) {
		if _, ok := clen[f[0]]; !ok {
			return
		}
		s, e := atoi(f[1])+1, atoi(f[2])
		blk[f[0]] = append(blk[f[0]], [2]int64{s, e})
		nBlk++
		if blkTile[f[0]] == nil {
			blkTile[f[0]] = map[int64]bool{}
		}
		for t := s/500 + 1; t <= e/500+1; t++ {
			blkTile[f[0]][t] = true
		}
	})

	// ---- peaks: idx is the order within each chrom, as addPeakSet assigns it
	type peak struct{ s, e, idx int64 }
	peaks := map[string][]peak{}
	pw, _ := os.Create(*out + "peaks_archr.tsv")
	fmt.Fprintln(pw, "chr\tstart\tend\tidx")
	nPk := 0
	lines(*peaksF, func(f []string) {
		if _, ok := clen[f[0]]; !ok {
			return
		}
		s, e := atoi(f[1])+1, atoi(f[2])
		l := peaks[f[0]]
		if len(l) > 0 && s <= l[len(l)-1].e {
			die("peaks are not sorted and disjoint on %s at %d", f[0], s)
		}
		pk := peak{s, e, int64(len(l) + 1)}
		peaks[f[0]] = append(l, pk)
		fmt.Fprintf(pw, "%s\t%d\t%d\t%d\n", f[0], s, e, pk.idx)
		nPk++
	})
	pw.Close()

	covW, covF, covP, covB := map[string]*cov{}, map[string]*cov{}, map[string]*cov{}, map[string]*cov{}
	for _, c := range chroms {
		a, b, d, e := buildCov(win[c]), buildCov(flk[c]), buildCov(prom[c]), buildCov(blk[c])
		covW[c], covF[c], covP[c], covB[c] = &a, &b, &d, &e
	}
	fmt.Printf("annotation\tprotein_coding genes %d  transcripts %d  unique TSS %d  blacklist regions %d  peaks %d\n",
		len(gorder), len(torder), nTSS, nBlk, nPk)

	// ---- the sweep. The file is sorted by start within each chrom, so every insertion still to
	// come is >= start0+1: a tile below (start0+1)/500+1, or a peak ending before start0+1, is final.
	tileSt, peakSt, peak4St := map[string]*mstat{}, map[string]*mstat{}, map[string]*mstat{}
	for _, c := range chroms {
		tileSt[c], peakSt[c], peak4St[c] = &mstat{}, &mstat{}, &mstat{}
	}
	var cur string
	seen := map[string]bool{}
	var lastS int64
	openT := map[int64]map[int]int64{}
	var tLo int64 = 1 << 62
	openP := map[int]map[int]int64{} // index into peaks[cur]
	var pl []peak
	var pcur int
	flushT := func(below int64) {
		for t := tLo; t < below && len(openT) > 0; t++ {
			if m, ok := openT[t]; ok {
				for j, x := range m {
					tileSt[cur].add(t, j, x)
				}
				delete(openT, t)
			}
		}
		if below > tLo {
			tLo = below
		}
	}
	flushP := func(k int) {
		for j, x := range openP[k] {
			peakSt[cur].add(pl[k].idx, j, x)
			x4 := x
			if x4 > 4 {
				x4 = 4
			}
			peak4St[cur].add(pl[k].idx, j, x4)
		}
		delete(openP, k)
	}
	flushAll := func() {
		if cur == "" {
			return
		}
		flushT(1 << 62)
		for k := range openP {
			flushP(k)
		}
	}
	var nRec, nOff, nNonCell, nWin, nKept int64
	chrRec := map[string]int64{}
	r := bufio.NewReaderSize(open(*frag), 1<<22)
	for {
		ln, err := r.ReadSlice('\n')
		if len(ln) == 0 && err != nil {
			if err != io.EOF {
				die("read: %v", err)
			}
			break
		}
		if ln[0] == '#' {
			continue
		}
		f := bytes.SplitN(bytes.TrimRight(ln, "\r\n"), []byte{'\t'}, 5)
		if len(f) < 4 {
			die("short line: %q", ln)
		}
		nRec++
		chr := string(f[0])
		s0, e := atoi(string(f[1])), atoi(string(f[2]))
		j := cell[string(f[3])]
		if chr != cur {
			flushAll()
			if seen[chr] {
				die("chrom %s appears in two blocks: the file is not sorted", chr)
			}
			seen[chr] = true
			cur, lastS, tLo = chr, -1, 1<<62
			pl, pcur = peaks[chr], 0
		}
		if s0 < lastS {
			die("%s: start %d after %d: not sorted", chr, s0, lastS)
		}
		lastS = s0
		if j == 0 {
			nNonCell++
			continue
		}
		c := &cc[j]
		c.recAll++
		if _, ok := clen[chr]; !ok {
			nOff++
			continue
		}
		c.recChrom++
		w := e - s0
		if w < 10 || w > 2000 {
			nWin++
			continue
		}
		nKept++
		chrRec[chr]++
		c.kept++
		if len(pl) > 0 {
			c.peakChrKept++
		}
		switch k := w/147 + 1; {
		case k == 1:
			c.mono++
		case k == 2:
			c.di++
		default:
			c.multi++
		}
		// final tiles and peaks
		if !excluded[chr] {
			flushT((s0+1)/500 + 1)
		}
		for k := range openP {
			if pl[k].e < s0+1 {
				flushP(k)
			}
		}
		for _, p := range [2]int64{s0 + 1, e} {
			c.nW += covW[chr].at(p)
			c.nF += covF[chr].at(p)
			c.prom += covP[chr].at(p)
			c.black += covB[chr].at(p)
			if excluded[chr] {
				c.exclIns++
			} else {
				t := p/500 + 1
				if blkTile[chr][t] {
					c.blkTileIns++
				} else {
					c.tileIns++
					m := openT[t]
					if m == nil {
						m = map[int]int64{}
						openT[t] = m
						if t < tLo {
							tLo = t
						}
					}
					m[j]++
				}
			}
			// the peak containing p, if any: peaks are disjoint and sorted
			for pcur < len(pl) && pl[pcur].e < s0+1 {
				pcur++
			}
			for k := pcur; k < len(pl) && pl[k].s <= p; k++ {
				if pl[k].e >= p {
					if openP[k] == nil {
						openP[k] = map[int]int64{}
					}
					openP[k][j]++
					c.peakIns++
					break
				}
			}
		}
	}
	flushAll()

	// ---- write
	cw, _ := os.Create(*out + "cells_recount.tsv")
	fmt.Fprintln(cw, "barcode\trec_all\trec_chrom\tkept\tmono\tdi\tmulti\tnW\tnF\tpromoter\tblacklist\ttile_ins\tblacklisted_tile_ins\texcluded_chr_ins\tpeak_ins\tpeak_chr_kept")
	bad := 0
	for j, b := range cells {
		c := cc[j+1]
		if c.tileIns+c.blkTileIns+c.exclIns != 2*c.kept {
			bad++
		}
		fmt.Fprintf(cw, "%s\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%d\n", b, c.recAll, c.recChrom, c.kept,
			c.mono, c.di, c.multi, c.nW, c.nF, c.prom, c.black, c.tileIns, c.blkTileIns, c.exclIns, c.peakIns, c.peakChrKept)
	}
	cw.Close()
	if bad > 0 {
		die("%d cells break tile + blacklisted-tile + excluded-chrom insertions == 2 x kept", bad)
	}
	sw, _ := os.Create(*out + "matrix_stats.tsv")
	fmt.Fprintln(sw, "matrix\tchr\tnnz\tsum\tsumsq\tck")
	for _, ch := range chroms {
		if !excluded[ch] {
			t := tileSt[ch]
			fmt.Fprintf(sw, "TileMatrix\t%s\t%d\t%d\t%d\t%d\n", ch, t.nnz, t.sum, t.sq, t.ck)
		}
	}
	for _, ch := range chroms {
		if len(peaks[ch]) > 0 {
			t, u := peakSt[ch], peak4St[ch]
			fmt.Fprintf(sw, "PeakInsertions\t%s\t%d\t%d\t%d\t%d\n", ch, t.nnz, t.sum, t.sq, t.ck)
			fmt.Fprintf(sw, "PeakMatrix\t%s\t%d\t%d\t%d\t%d\n", ch, u.nnz, u.sum, u.sq, u.ck)
		}
	}
	sw.Close()
	kw, _ := os.Create(*out + "chrom_kept.tsv")
	for _, ch := range chroms {
		fmt.Fprintf(kw, "%s\t%d\t%d\n", ch, clen[ch], chrRec[ch])
	}
	kw.Close()
	fmt.Printf("fragments\trecords %d  non-cell barcodes %d  cell records off -sizes chroms %d  outside 10..2000 bp %d  kept %d\n",
		nRec, nNonCell, nOff, nWin, nKept)
	fmt.Println("RECOUNT OK")
}
