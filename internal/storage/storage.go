package storage

import (
	"crypto/rand"
	"errors"
	"math/big"
	"sync"
	"time"
	"urlshortener/internal/models"
)

const (
	codeLength = 6
	alphabet   = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
	maxRetries = 10
)

var (
	// ErrNotFound возвращается, если код не найден.
	ErrNotFound = errors.New("url not found")
	// ErrCodeGeneration возвращается при неудачной генерации уникального кода.
	ErrCodeGeneration = errors.New("failed to generate unique code")
)

// Storage описывает методы хранилища.
type Storage interface {
	Create(original string) (models.URL, error)
	GetByCode(code string) (models.URL, error)
	GetStats(code string) (models.Stats, error)
}

// memoryStorage потокобезопасно хранит ссылки в памяти.
type memoryStorage struct {
	mu   sync.RWMutex
	urls map[string]models.URL
}

// NewMemoryStorage создает in-memory реализацию хранилища.
func NewMemoryStorage() Storage {
	return &memoryStorage{
		urls: make(map[string]models.URL),
	}
}

// Create сохраняет URL и генерирует для него уникальный код.
func (s *memoryStorage) Create(original string) (models.URL, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	for i := 0; i < maxRetries; i++ {
		code, err := generateCode(codeLength)
		if err != nil {
			return models.URL{}, err
		}
		if _, exists := s.urls[code]; exists {
			continue
		}

		item := models.URL{
			Code:      code,
			Original:  original,
			CreatedAt: nowUTC(),
			Clicks:    0,
		}
		s.urls[code] = item
		return item, nil
	}

	return models.URL{}, ErrCodeGeneration
}

// GetByCode возвращает оригинальный URL и увеличивает счетчик переходов.
func (s *memoryStorage) GetByCode(code string) (models.URL, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	item, ok := s.urls[code]
	if !ok {
		return models.URL{}, ErrNotFound
	}
	item.Clicks++
	s.urls[code] = item
	return item, nil
}

// GetStats возвращает статистику по коду без изменения счетчиков.
func (s *memoryStorage) GetStats(code string) (models.Stats, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()

	item, ok := s.urls[code]
	if !ok {
		return models.Stats{}, ErrNotFound
	}

	return models.Stats{
		Code:      item.Code,
		Original:  item.Original,
		Clicks:    item.Clicks,
		CreatedAt: item.CreatedAt,
	}, nil
}

// generateCode создает случайную строку фиксированной длины.
func generateCode(length int) (string, error) {
	result := make([]byte, length)
	max := big.NewInt(int64(len(alphabet)))

	for i := 0; i < length; i++ {
		n, err := rand.Int(rand.Reader, max)
		if err != nil {
			return "", err
		}
		result[i] = alphabet[n.Int64()]
	}
	return string(result), nil
}

// nowUTC вынесен отдельно для читаемости.
func nowUTC() time.Time {
	return time.Now().UTC()
}
