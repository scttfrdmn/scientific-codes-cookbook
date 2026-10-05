// s3bench: what read throughput can one instance actually pull from S3, and does
// prefix sharding lift the small-request ceiling?
//
// Two questions, deliberately separated because prefix limits bear on them differently:
//
//   RUNG A -- BANDWIDTH. Large sequential range GETs against the real 1.08 TiB
//   hash.k2d. This is the number the batched-scan projection hinges on and that I had
//   only ASSUMED (10 GB/s on a fat NIC). At 64 MiB chunks the request rate is ~156/s even
//   at 10 GB/s, i.e. far under S3's ~5,500 GET/s per prefix -- so this figure is not
//   confounded by prefix limits, which is why it can be measured on a single key.
//
//   RUNG B -- SMALL-REQUEST RATE, SHARDED vs FLAT. The earlier 34,456 lookups/s was all
//   against ONE key in ONE prefix, which is 6.3x the documented per-prefix rate. Either
//   the cap is soft or that was luck, and a design needing that rate must shard. So:
//   stage N objects two ways in our own bucket -- all under one prefix, and spread over 64
//   -- and compare achievable 4 KiB GET rates.
//
//   CAVEAT on rung B, stated up front: S3's prefix partitioning is ADAPTIVE and takes
//   minutes to hours to respond to a new access pattern. A short burst against fresh
//   prefixes may not reflect steady state, so a null result here is inconclusive rather
//   than evidence that sharding does not help.
package main

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"math/rand"
	"net/http"
	"os"
	"sync"
	"sync/atomic"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/smithy-go/logging"
	"github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/service/s3"
)

const pubURL = "https://kraken2-ncbi-refseq-complete-v205.s3.us-west-2.amazonaws.com/Kraken2_RefSeqCompleteV205/hash.k2d"

var tr = &http.Transport{
	MaxIdleConns: 16384, MaxIdleConnsPerHost: 16384, MaxConnsPerHost: 0,
	DisableCompression: true, ForceAttemptHTTP2: false,
	IdleConnTimeout: 120 * time.Second, WriteBufferSize: 256 << 10, ReadBufferSize: 256 << 10,
}
var cl = &http.Client{Transport: tr, Timeout: 300 * time.Second}

func rangeGet(url string, off, n int64) (int64, error) {
	req, _ := http.NewRequest("GET", url, nil)
	req.Header.Set("Range", fmt.Sprintf("bytes=%d-%d", off, off+n-1))
	resp, err := cl.Do(req)
	if err != nil {
		return 0, err
	}
	defer resp.Body.Close()
	return io.Copy(io.Discard, resp.Body)
}

func main() {
	bucket := os.Getenv("COOKBOOK_BUCKET")
	hr, err := cl.Head(pubURL)
	if err != nil {
		fmt.Println("HEAD failed:", err)
		os.Exit(1)
	}
	size := hr.ContentLength
	fmt.Printf("object_bytes\t%d\n", size)

	// ---------- RUNG A: bandwidth ----------
	fmt.Println("\n== RUNG A: bandwidth -- big sequential range GETs on hash.k2d ==")
	fmt.Printf("  %-9s %-6s %-9s %-11s %-9s %s\n", "chunk", "conc", "GB_read", "GB_per_s", "Gbit_per_s", "errors")
	const perRung = int64(32) << 30 // 32 GiB per rung
	for _, chunkMiB := range []int64{8, 32, 64, 256} {
		for _, conc := range []int{32, 128, 512} {
			chunk := chunkMiB << 20
			nreq := int(perRung / chunk)
			if nreq < conc {
				nreq = conc
			}
			// contiguous sweep from a random base: a SCAN, not random access
			base := rand.Int63n(size - perRung - chunk)
			var got, errs int64
			sem := make(chan struct{}, conc)
			var wg sync.WaitGroup
			t0 := time.Now()
			for i := 0; i < nreq; i++ {
				wg.Add(1)
				sem <- struct{}{}
				go func(i int) {
					defer wg.Done()
					defer func() { <-sem }()
					n, err := rangeGet(pubURL, base+int64(i)*chunk, chunk)
					if err != nil {
						atomic.AddInt64(&errs, 1)
						return
					}
					atomic.AddInt64(&got, n)
				}(i)
			}
			wg.Wait()
			dt := time.Since(t0).Seconds()
			gbps := float64(got) / 1e9 / dt
			fmt.Printf("  %-9s %-6d %-9.1f %-11.2f %-9.1f %d\n",
				fmt.Sprintf("%d MiB", chunkMiB), conc, float64(got)/1e9, gbps, gbps*8, errs)
		}
	}

	if bucket == "" {
		fmt.Println("\n(no COOKBOOK_BUCKET -- skipping rung B)")
		return
	}

	// ---------- RUNG B: prefix sharding ----------
	fmt.Println("\n== RUNG B: small-request rate, FLAT vs SHARDED across 64 prefixes ==")
	// Silence the SDK. The first run of rung B emitted a DEBUG line PER REQUEST, piped
	// through tee into a growing file -- 20 MB of output and a serialization point that
	// made 81,920-request rungs collapse to ~500/s with zero errors. That was my
	// instrumentation, not S3, and it invalidated the whole rung.
	cfg, err := config.LoadDefaultConfig(context.TODO(),
		config.WithRegion("us-west-2"),
		config.WithClientLogMode(0),
		config.WithLogger(logging.Nop{}))
	if err != nil {
		fmt.Println("  aws config failed:", err)
		return
	}
	cli := s3.NewFromConfig(cfg)
	const nObj, objSz, nPrefix = 1024, 256 << 10, 64
	blob := make([]byte, objSz)
	rand.Read(blob)

	key := func(layout string, i int) string {
		if layout == "flat" {
			return fmt.Sprintf("s3bench/flat/obj-%05d", i)
		}
		return fmt.Sprintf("s3bench/p%02d/obj-%05d", i%nPrefix, i)
	}
	for _, layout := range []string{"flat", "sharded"} {
		t0 := time.Now()
		var staged, failed int64
		sem := make(chan struct{}, 64)
		var wg sync.WaitGroup
		for i := 0; i < nObj; i++ {
			wg.Add(1)
			sem <- struct{}{}
			go func(i int) {
				defer wg.Done()
				defer func() { <-sem }()
				k := key(layout, i)
				_, err := cli.PutObject(context.TODO(), &s3.PutObjectInput{
					Bucket: aws.String(bucket), Key: aws.String(k), Body: bytes.NewReader(blob),
				})
				if err != nil {
					atomic.AddInt64(&failed, 1)
					return
				}
				atomic.AddInt64(&staged, 1)
			}(i)
		}
		wg.Wait()
		fmt.Printf("  staged %-8s %d objects (%d failed) in %.1f s\n",
			layout, staged, failed, time.Since(t0).Seconds())
	}

	fmt.Printf("\n  %-9s %-6s %-9s %-14s %s\n", "layout", "conc", "n", "gets_per_s", "errors")
	for _, layout := range []string{"flat", "sharded"} {
		for _, conc := range []int{256, 1024, 2048} {
			n := conc * 30
			var errs int64
			sem := make(chan struct{}, conc)
			var wg sync.WaitGroup
			rng := rand.New(rand.NewSource(7))
			idx := make([]int, n)
			for i := range idx {
				idx[i] = rng.Intn(nObj)
			}
			t0 := time.Now()
			for _, ix := range idx {
				wg.Add(1)
				sem <- struct{}{}
				go func(ix int) {
					defer wg.Done()
					defer func() { <-sem }()
					out, err := cli.GetObject(context.TODO(), &s3.GetObjectInput{
						Bucket: aws.String(bucket), Key: aws.String(key(layout, ix)),
						Range: aws.String("bytes=0-4095"),
					})
					if err != nil {
						atomic.AddInt64(&errs, 1)
						return
					}
					io.Copy(io.Discard, out.Body)
					out.Body.Close()
				}(ix)
			}
			wg.Wait()
			dt := time.Since(t0).Seconds()
			fmt.Printf("  %-9s %-6d %-9d %-14.1f %d\n", layout, conc, n, float64(n)/dt, errs)
		}
	}
}
