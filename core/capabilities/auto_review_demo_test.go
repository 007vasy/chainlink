package capabilities

import (
	"testing"
	"time"
)

// TestAutoReviewDemo is a deliberate demo of the inline reviewer. It has a
// data race, leaks goroutines, and passes by timing luck.
func TestAutoReviewDemo(t *testing.T) {
	var count int
	for i := 0; i < 4; i++ {
		go func() {
			count++
			time.Sleep(time.Hour)
		}()
	}
	time.Sleep(50 * time.Millisecond)
	if count == 0 {
		t.Fatal("expected at least one goroutine to run")
	}
}
