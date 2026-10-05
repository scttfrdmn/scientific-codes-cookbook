// s3depth: how many concurrent random range GETs can one instance actually sustain?
//
// WHY GO AND NOT PYTHON. The first version of this sweep was Python/boto3 and plateaued at
// ~790 lookups/s on a c8g.2xlarge. I attributed that to "my client" WITHOUT TESTING IT, which
// was a guess dressed as a finding. Two variables were conflated: the client (GIL, per-request
// object overhead) and the instance (PPS and concurrent-connection allowances, and "Up to
// 15 Gigabit" being burstable). Goroutines have no GIL and this issues plain HTTPS range GETs
// with a tuned Transport, so client overhead is near zero -- which makes the remaining ceiling
// attributable to the instance.
//
// The bucket is public (RODA), so no signing is needed and there is no SDK dependency at all.
package main

import (
	"fmt"
	"io"
	"math/rand"
	"net/http"
	"os"
	"strconv"
	"sync"
	"sync/atomic"
	"time"
)

const url = "https://kraken2-ncbi-refseq-complete-v205.s3.us-west-2.amazonaws.com/Kraken2_RefSeqCompleteV205/hash.k2d"

func main() {
	probe := 4096
	if len(os.Args) > 1 {
		probe, _ = strconv.Atoi(os.Args[1])
	}
	tr := &http.Transport{
		MaxIdleConns:        8192,
		MaxIdleConnsPerHost: 8192,
		MaxConnsPerHost:     0, // unlimited; the semaphore is the only throttle
		DisableCompression:  true,
		ForceAttemptHTTP2:   false, // one TCP conn per request stream, like boto3
		IdleConnTimeout:     90 * time.Second,
	}
	cl := &http.Client{Transport: tr, Timeout: 120 * time.Second}

	// size via HEAD
	hr, err := cl.Head(url)
	if err != nil {
		fmt.Println("HEAD failed:", err)
		os.Exit(1)
	}
	size := hr.ContentLength
	fmt.Printf("object_bytes\t%d\nprobe_bytes\t%d\n", size, probe)

	get := func(off int64) (int64, error) {
		req, _ := http.NewRequest("GET", url, nil)
		req.Header.Set("Range", fmt.Sprintf("bytes=%d-%d", off, off+int64(probe)-1))
		resp, err := cl.Do(req)
		if err != nil {
			return 0, err
		}
		defer resp.Body.Close()
		n, err := io.Copy(io.Discard, resp.Body)
		return n, err
	}

	fmt.Printf("  %-6s %-8s %-14s %-12s %-10s %s\n", "depth", "n", "lookups_per_s", "MB_per_s", "errors", "vs_depth_1")
	var base float64
	for _, c := range []struct{ depth, n int }{
		{1, 60}, {8, 480}, {32, 1920}, {128, 7680},
		{512, 20480}, {2048, 40960}, {8192, 65536},
	} {
		rng := rand.New(rand.NewSource(42)) // same offsets every depth
		offs := make([]int64, c.n)
		for i := range offs {
			offs[i] = rng.Int63n(size - int64(probe))
		}
		var bytes, errs int64
		sem := make(chan struct{}, c.depth)
		var wg sync.WaitGroup
		t0 := time.Now()
		for _, o := range offs {
			wg.Add(1)
			sem <- struct{}{}
			go func(off int64) {
				defer wg.Done()
				defer func() { <-sem }()
				n, err := get(off)
				if err != nil {
					atomic.AddInt64(&errs, 1)
					return
				}
				atomic.AddInt64(&bytes, n)
			}(o)
		}
		wg.Wait()
		dt := time.Since(t0).Seconds()
		rate := float64(c.n) / dt
		if base == 0 {
			base = rate
		}
		fmt.Printf("  %-6d %-8d %-14.1f %-12.2f %-10d %.1fx\n",
			c.depth, c.n, rate, float64(bytes)/1e6/dt, errs, rate/base)
	}
}
