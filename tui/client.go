package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"strings"
	"time"
)

// Package contains the fields displayed by the TUI. COPR timestamps can be
// numbers, strings, or null; formatting them is the presentation layer's job.
type Package struct {
	Name            string `json:"name"`
	COPRDescription string `json:"copr_description"`
	Reason          string `json:"reason"`
	Version         string `json:"version"`
	SubmittedOn     any    `json:"submitted_on"`
	EndedOn         any    `json:"ended_on"`
	COPRProject     string `json:"copr_project"`
}

type searchClient struct {
	baseURL string
	http    *http.Client
}

func newSearchClient(baseURL string) searchClient {
	return searchClient{
		baseURL: strings.TrimRight(baseURL, "/"),
		http: &http.Client{
			Timeout: 60 * time.Second,
			CheckRedirect: func(req *http.Request, via []*http.Request) error {
				return http.ErrUseLastResponse
			},
		},
	}
}

func (c searchClient) search(ctx context.Context, query string) ([]Package, error) {
	endpoint, err := url.Parse(c.baseURL + "/search")
	if err != nil {
		return nil, fmt.Errorf("Error: %w", err)
	}
	params := endpoint.Query()
	params.Set("q", query)
	params.Set("limit", "7")
	endpoint.RawQuery = params.Encode()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint.String(), nil)
	if err != nil {
		return nil, fmt.Errorf("Error: %w", err)
	}
	resp, err := c.http.Do(req)
	if err != nil {
		var networkError *net.OpError
		if errors.As(err, &networkError) && networkError.Op == "dial" {
			return nil, fmt.Errorf("Cannot reach backend at %s — is the stack running? (podman-compose up -d)", c.baseURL)
		}
		return nil, fmt.Errorf("Error: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, fmt.Errorf("Backend error: %d", resp.StatusCode)
	}
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("Error: %w", err)
	}
	var packages []Package
	if err := json.Unmarshal(body, &packages); err != nil {
		return nil, fmt.Errorf("Error: %w", err)
	}
	return packages, nil
}
