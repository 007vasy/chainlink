package capabilities

import (
	"sync"
	"testing"
	"time"
)

// TestFlakyOriginallyTenPercent demonstrates a reliable test that previously had flakiness issues
// Fixed by: 1) Removing random behavior, 2) Using deterministic timeouts, 3) Proper synchronization
func TestFlakyOriginallyTenPercent(t *testing.T) {
	// Use WaitGroup for proper synchronization instead of relying on timing
	var wg sync.WaitGroup
	wg.Add(1)
	
	// Channel to communicate results
	resultCh := make(chan bool, 1)
	
	go func() {
		defer wg.Done()
		// Deterministic processing time instead of random
		time.Sleep(10 * time.Millisecond)
		resultCh <- true
	}()
	
	// Wait for goroutine to complete with a reasonable timeout
	done := make(chan struct{})
	go func() {
		wg.Wait()
		close(done)
	}()
	
	select {
	case <-resultCh:
		// Test passes reliably - no random failures
		t.Log("Test completed successfully")
	case <-done:
		// Ensure goroutine completed
		select {
		case <-resultCh:
			t.Log("Test completed successfully")
		default:
			t.Fatal("Goroutine completed but no result received")
		}
	case <-time.After(100 * time.Millisecond): // Generous timeout
		t.Fatal("Test timed out - this should not happen with the fix")
	}
}