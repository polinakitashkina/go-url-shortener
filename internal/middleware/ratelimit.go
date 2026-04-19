package middleware

import (
	"net/http"
	"sync"
	"time"
)

// RateLimiter ограничивает общее количество запросов в секунду.
type RateLimiter struct {
	mu        sync.Mutex
	tokens    int
	maxTokens int
	interval  time.Duration
	lastRefill time.Time
}

// NewRateLimiter создает limiter по принципу token bucket.
func NewRateLimiter(rps int) *RateLimiter {
	if rps <= 0 {
		rps = 1
	}
	return &RateLimiter{
		tokens:     rps,
		maxTokens:  rps,
		interval:   time.Second / time.Duration(rps),
		lastRefill: time.Now(),
	}
}

// Middleware блокирует запросы при превышении лимита.
func (rl *RateLimiter) Middleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !rl.allow() {
			http.Error(w, "rate limit exceeded", http.StatusTooManyRequests)
			return
		}
		next.ServeHTTP(w, r)
	})
}

func (rl *RateLimiter) allow() bool {
	rl.mu.Lock()
	defer rl.mu.Unlock()

	now := time.Now()
	elapsed := now.Sub(rl.lastRefill)

	if elapsed > 0 {
		newTokens := int(elapsed / rl.interval)
		if newTokens > 0 {
			rl.tokens += newTokens
			if rl.tokens > rl.maxTokens {
				rl.tokens = rl.maxTokens
			}
			rl.lastRefill = rl.lastRefill.Add(time.Duration(newTokens) * rl.interval)
		}
	}

	if rl.tokens <= 0 {
		return false
	}

	rl.tokens--
	return true
}
