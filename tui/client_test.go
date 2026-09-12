package main

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestSearchRequestAndResponse(t *testing.T) {
	query := "video & audio / café + recorder?"
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet || r.URL.Path != "/search" {
			t.Errorf("request = %s %s", r.Method, r.URL.Path)
		}
		if r.URL.Query().Get("q") != query || r.URL.Query().Get("limit") != "7" || len(r.URL.Query()) != 2 {
			t.Errorf("query parameters = %v", r.URL.Query())
		}
		fmt.Fprint(w, `[{"name":"editor","copr_description":"Edit videos","reason":"Matches your query","version":"1.2","submitted_on":1700000000,"ended_on":"1700001000","copr_project":"owner/project","score":0.9}]`)
	}))
	defer server.Close()
	client := newSearchClient(server.URL + "/")
	if client.http.Timeout != 120*time.Second {
		t.Fatalf("request timeout = %v", client.http.Timeout)
	}
	packages, err := client.search(context.Background(), query)
	if err != nil {
		t.Fatal(err)
	}
	if len(packages) != 1 || packages[0].Name != "editor" || packages[0].COPRDescription != "Edit videos" || packages[0].Reason != "Matches your query" || packages[0].Version != "1.2" || packages[0].COPRProject != "owner/project" {
		t.Fatalf("packages = %#v", packages)
	}
	if renderCell("submitted_on", packages[0]) != time.Unix(1700000000, 0).In(time.Local).Format("2006-01-02") || renderCell("ended_on", packages[0]) != time.Unix(1700001000, 0).In(time.Local).Format("2006-01-02") {
		t.Fatalf("timestamps = %#v", packages[0])
	}
}

func TestSearchFailures(t *testing.T) {
	for _, test := range []struct {
		name   string
		status int
		body   string
		want   string
	}{
		{"server error", 503, `unavailable`, "Backend error: 503"},
		{"client error", 422, `{}`, "Backend error: 422"},
		{"redirect", 302, ``, "Backend error: 302"},
		{"invalid JSON", 200, `not json`, "Error:"},
		{"wrong response shape", 200, `{"name":"editor"}`, "Error:"},
		{"trailing JSON", 200, `[] {}`, "Error:"},
	} {
		t.Run(test.name, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.Header().Set("Location", "/redirected")
				w.WriteHeader(test.status)
				fmt.Fprint(w, test.body)
			}))
			defer server.Close()
			packages, err := newSearchClient(server.URL).search(context.Background(), "editor")
			if err == nil || !strings.HasPrefix(err.Error(), test.want) || packages != nil {
				t.Fatalf("got %#v, %v; want %q", packages, err, test.want)
			}
		})
	}
}

func TestConnectionError(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {}))
	server.Close()
	_, err := newSearchClient(server.URL).search(context.Background(), "editor")
	if err == nil || !strings.Contains(err.Error(), "Cannot reach backend at "+server.URL) || !strings.Contains(err.Error(), "podman-compose up -d") {
		t.Fatalf("connection error = %v", err)
	}
}

func TestSearchTimeoutAndCancellation(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		<-r.Context().Done()
	}))
	defer server.Close()
	client := newSearchClient(server.URL)
	client.http.Timeout = 20 * time.Millisecond
	_, err := client.search(context.Background(), "editor")
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("timeout error = %v", err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	_, err = client.search(ctx, "editor")
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("cancelled request error = %v", err)
	}
}
