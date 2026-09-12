package main

import (
	"fmt"
	"os"
	"path/filepath"
	"testing"
)

func TestHistoryFilePathUsesXDGStateHome(t *testing.T) {
	dir := t.TempDir()
	t.Setenv("XDG_STATE_HOME", dir)
	want := filepath.Join(dir, "drillbit", "history.json")
	if got := historyFilePath(); got != want {
		t.Fatalf("historyFilePath() = %q, want %q", got, want)
	}
}

func TestHistoryFilePathFallsBackToLocalState(t *testing.T) {
	t.Setenv("XDG_STATE_HOME", "")
	home := t.TempDir()
	t.Setenv("HOME", home)
	want := filepath.Join(home, ".local", "state", "drillbit", "history.json")
	if got := historyFilePath(); got != want {
		t.Fatalf("historyFilePath() = %q, want %q", got, want)
	}
}

func TestHistoryAddDedupesConsecutiveRepeats(t *testing.T) {
	h := History{path: filepath.Join(t.TempDir(), "history.json")}
	for _, query := range []string{"video editor", "video editor", "screen recorder", "screen recorder", "video editor"} {
		if err := h.add(query); err != nil {
			t.Fatalf("add(%q) error: %v", query, err)
		}
	}
	want := []string{"video editor", "screen recorder", "video editor"}
	if len(h.entries) != len(want) {
		t.Fatalf("entries = %v, want %v", h.entries, want)
	}
	for i, query := range want {
		if h.entries[i] != query {
			t.Fatalf("entries = %v, want %v", h.entries, want)
		}
	}
}

func TestHistoryAddCapsRetention(t *testing.T) {
	h := History{path: filepath.Join(t.TempDir(), "history.json")}
	for i := range maxHistoryEntries + 50 {
		if err := h.add(fmt.Sprintf("query-%d", i)); err != nil {
			t.Fatalf("add error: %v", err)
		}
	}
	if len(h.entries) != maxHistoryEntries {
		t.Fatalf("len(entries) = %d, want %d", len(h.entries), maxHistoryEntries)
	}
	// The oldest entries must have been dropped, keeping the newest.
	last := fmt.Sprintf("query-%d", maxHistoryEntries+50-1)
	if h.entries[len(h.entries)-1] != last {
		t.Fatalf("newest entry = %q, want %q", h.entries[len(h.entries)-1], last)
	}
}

func TestHistoryPersistsAcrossInstances(t *testing.T) {
	path := filepath.Join(t.TempDir(), "history.json")
	h := History{path: path}
	for _, query := range []string{"video editor", "screen recorder"} {
		if err := h.add(query); err != nil {
			t.Fatalf("add error: %v", err)
		}
	}
	reloaded := History{path: path}
	reloaded.load()
	if len(reloaded.entries) != 2 || reloaded.entries[0] != "video editor" || reloaded.entries[1] != "screen recorder" {
		t.Fatalf("reloaded entries = %v", reloaded.entries)
	}
}

func TestHistorySaveUsesOwnerOnlyPermissions(t *testing.T) {
	path := filepath.Join(t.TempDir(), "nested", "history.json")
	h := History{path: path}
	if err := h.add("video editor"); err != nil {
		t.Fatalf("add error: %v", err)
	}
	info, err := os.Stat(path)
	if err != nil {
		t.Fatalf("stat error: %v", err)
	}
	if perm := info.Mode().Perm(); perm != 0o600 {
		t.Fatalf("file perm = %o, want %o", perm, 0o600)
	}
	dirInfo, err := os.Stat(filepath.Dir(path))
	if err != nil {
		t.Fatalf("stat dir error: %v", err)
	}
	if perm := dirInfo.Mode().Perm(); perm != 0o700 {
		t.Fatalf("dir perm = %o, want %o", perm, 0o700)
	}
}

func TestHistoryLoadHandlesMissingOrCorruptFile(t *testing.T) {
	h := History{path: filepath.Join(t.TempDir(), "missing.json")}
	h.load()
	if h.entries != nil {
		t.Fatalf("entries from missing file = %v, want nil", h.entries)
	}
	path := filepath.Join(t.TempDir(), "corrupt.json")
	if err := os.WriteFile(path, []byte("not json"), 0o600); err != nil {
		t.Fatalf("write error: %v", err)
	}
	corrupt := History{path: path}
	corrupt.load()
	if corrupt.entries != nil {
		t.Fatalf("entries from corrupt file = %v, want nil", corrupt.entries)
	}
}
