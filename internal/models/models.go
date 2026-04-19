package models

import "time"

// URL хранит данные о сокращенной ссылке.
type URL struct {
	Code      string
	Original  string
	CreatedAt time.Time
	Clicks    int
}

// Stats возвращается из эндпоинта статистики.
type Stats struct {
	Code      string    `json:"code"`
	Original  string    `json:"original_url"`
	Clicks    int       `json:"clicks"`
	CreatedAt time.Time `json:"created_at"`
}
