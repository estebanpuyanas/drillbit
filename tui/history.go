package main

import (
	"encoding/json"
	"os"
	"path/filepath"
)

// maxHistoryEntries caps retention so the history file cannot grow unbounded.
const maxHistoryEntries = 200

const historyFileName = "history.json"

// History persists past search queries to an XDG state file, shell-history
// style, so the TUI can recall them across restarts.
type History struct {
	path    string
	entries []string
}

func newHistory() History {
	return History{path: historyFilePath()}
}

// historyFilePath resolves an XDG-compliant location for the history file:
// $XDG_STATE_HOME/drillbit/history.json, falling back to ~/.local/state when
// XDG_STATE_HOME is unset. Returns "" if the home directory cannot be
// determined, in which case history is kept in memory only for the session.
func historyFilePath() string {
	stateHome := os.Getenv("XDG_STATE_HOME")
	if stateHome == "" {
		home, err := os.UserHomeDir()
		if err != nil {
			return ""
		}
		stateHome = filepath.Join(home, ".local", "state")
	}
	return filepath.Join(stateHome, "drillbit", historyFileName)
}

// load reads previously persisted entries. A missing or corrupt file is not
// worth surfacing to the user; it just starts with empty history.
func (h *History) load() {
	if h.path == "" {
		return
	}
	data, err := os.ReadFile(h.path)
	if err != nil {
		return
	}
	var entries []string
	if err := json.Unmarshal(data, &entries); err != nil {
		return
	}
	h.entries = entries
}

// add appends query to the history, deduping an immediate repeat and capping
// retention at maxHistoryEntries, then persists the result to disk.
func (h *History) add(query string) error {
	if len(h.entries) > 0 && h.entries[len(h.entries)-1] == query {
		return nil
	}
	h.entries = append(h.entries, query)
	if len(h.entries) > maxHistoryEntries {
		h.entries = h.entries[len(h.entries)-maxHistoryEntries:]
	}
	return h.save()
}

// save writes entries to disk, creating the state directory if needed and
// keeping the file readable/writable by the owner only.
func (h *History) save() error {
	if h.path == "" {
		return nil
	}
	if err := os.MkdirAll(filepath.Dir(h.path), 0o700); err != nil {
		return err
	}
	data, err := json.Marshal(h.entries)
	if err != nil {
		return err
	}
	return os.WriteFile(h.path, data, 0o600)
}
