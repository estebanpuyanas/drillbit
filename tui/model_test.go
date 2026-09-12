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

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
	"github.com/charmbracelet/x/ansi"
)

func testModel() model {
	return newModel(context.Background(), newSearchClient("http://localhost:8000"))
}

func keyMsg(key string) tea.KeyMsg {
	keys := map[string]tea.KeyType{
		"enter": tea.KeyEnter, "esc": tea.KeyEsc, "f1": tea.KeyF1,
		"ctrl+l": tea.KeyCtrlL, "ctrl+q": tea.KeyCtrlQ, "up": tea.KeyUp,
		"down": tea.KeyDown, "left": tea.KeyLeft, "right": tea.KeyRight,
		"home": tea.KeyHome, "end": tea.KeyEnd, "pgdown": tea.KeyPgDown,
		"pgup": tea.KeyPgUp,
	}
	if keyType, ok := keys[key]; ok {
		return tea.KeyMsg{Type: keyType}
	}
	return tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune(key)}
}

func update(m model, msg tea.Msg) (model, tea.Cmd) {
	next, cmd := m.Update(msg)
	return next.(model), cmd
}

func press(m model, key string) model {
	next, _ := update(m, keyMsg(key))
	return next
}

func withResults(m model) model {
	next, _ := update(m, searchResultMsg{
		id: m.requestID, query: "video", packages: []Package{
			{Name: "editor", COPRDescription: "Edit videos", Reason: "Video editing", Version: "1.2", COPRProject: "owner/project"},
			{Name: "recorder", Reason: "Record the screen"},
		},
	})
	return next
}

func TestSearchInputFocusAndClear(t *testing.T) {
	m := testModel()
	if !m.input.Focused() || !strings.Contains(m.View(), searchLabel) || m.showingResults {
		t.Fatal("initial view must show and focus the labeled search input")
	}
	m = press(m, "c")
	if m.input.Value() != "c" || m.columnsOpen {
		t.Fatal("c must be typed into the search input")
	}
	m = press(m, "esc")
	m = press(m, "ignored")
	if m.input.Focused() || m.input.Value() != "c" {
		t.Fatal("Escape must blur search input")
	}
	m = press(m, "f1")
	m = press(m, "amera")
	if !m.input.Focused() || m.input.Value() != "camera" {
		t.Fatal("F1 must refocus input without clearing it")
	}
	m = press(m, "ctrl+l")
	if m.input.Value() != "" || !m.input.Focused() || m.status != "" {
		t.Fatal("Ctrl+L must clear and focus input")
	}
	m = press(m, "   ")
	m, cmd := update(m, keyMsg("enter"))
	if cmd != nil || m.loading {
		t.Fatal("blank queries must not submit")
	}
	m, cmd = update(m, keyMsg("ctrl+q"))
	if _, ok := cmd().(tea.QuitMsg); !ok {
		t.Fatal("Ctrl+Q must quit")
	}
}

func TestSubmitThroughHTTPToResults(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Query().Get("q") != "video editor" {
			t.Errorf("submitted query = %q", r.URL.Query().Get("q"))
		}
		fmt.Fprint(w, `[{"name":"editor","reason":"Edits video"}]`)
	}))
	defer server.Close()
	m := newModel(context.Background(), newSearchClient(server.URL))
	m = press(m, "  video editor  ")
	m, cmd := update(m, keyMsg("enter"))
	if !m.loading || m.showingResults || !strings.Contains(m.View(), `Drilling into packages for: "video editor"...`) || !strings.Contains(m.View(), "Searching…") {
		t.Fatal("submit must leave the search view visible with loading feedback")
	}
	// Execute the commands emitted by the real Update entry point, including the
	// HTTP command; no backend implementation or command closures are mocked.
	for _, command := range cmd().(tea.BatchMsg) {
		msg := command()
		m, _ = update(m, msg)
	}
	view := ansi.Strip(m.View())
	if m.loading || !m.showingResults || m.input.Focused() || !strings.Contains(view, `Found 1 package for "video editor"`) || !strings.Contains(view, "editor") || strings.Contains(view, searchLabel) {
		t.Fatalf("unexpected results view:\n%s", view)
	}
}

func TestResultsKeysAndColumnPersistence(t *testing.T) {
	m := withResults(testModel())
	if len(m.visibleColumns()) != 3 || !strings.Contains(m.resultsHeader, "Found 2 packages") {
		t.Fatal("default columns or result count changed")
	}
	m = press(m, "down")
	if m.rowCursor != 1 {
		t.Fatal("down must select the next result")
	}
	m = press(m, "c")
	for range 3 {
		m = press(m, "down")
	}
	m = press(m, " ")
	if !m.columnsOpen || !m.columns[3].selected || !strings.Contains(m.View(), "1.2") {
		t.Fatal("picker must toggle Version and rebuild the table")
	}
	m = press(m, "esc")
	if m.columnsOpen || !m.showingResults {
		t.Fatal("first Escape must close only the picker")
	}
	m = press(m, "esc")
	if m.showingResults || !m.input.Focused() || len(m.packages) != 0 || m.resultsHeader != "" {
		t.Fatal("Escape from results must return to a cleared search")
	}
	if !m.columns[3].selected {
		t.Fatal("column selections must survive clearing results")
	}
	for _, key := range []string{"f1", "ctrl+l"} {
		m = withResults(m)
		m = press(m, "c")
		m = press(m, key)
		if m.showingResults || m.columnsOpen || !m.input.Focused() || len(m.packages) != 0 {
			t.Fatalf("%s must clear results and focus search even from the picker", key)
		}
	}
}

func TestAllColumnsCanBeToggled(t *testing.T) {
	m := press(withResults(testModel()), "c")
	for i := range m.columns {
		if m.columns[i].selected {
			m = press(m, " ")
		}
		m = press(m, "down")
	}
	if len(m.visibleColumns()) != 0 || m.tableHeader != "" {
		t.Fatal("all columns may be deselected")
	}
	m = press(m, "home")
	for range len(m.columns) {
		m = press(m, "enter")
		m = press(m, "down")
	}
	visible := m.visibleColumns()
	if len(visible) != 7 {
		t.Fatalf("selected %d columns, want 7", len(visible))
	}
	for i, col := range defaultColumns() {
		if visible[i].key != col.key || !strings.Contains(ansi.Strip(m.tableHeader), col.label) {
			t.Fatal("column order must remain fixed")
		}
	}
	m = press(m, "c")
	if m.columnsOpen || !m.showingResults {
		t.Fatal("c must close the picker")
	}
}

func TestSearchErrorsAndEmptyResults(t *testing.T) {
	for _, test := range []struct {
		err  error
		want string
	}{
		{nil, `No packages found for "video". Try a different description.`},
		{errors.New("Backend error: 503"), "Backend error: 503"},
		{errors.New("Cannot reach backend at localhost:8000"), "Cannot reach backend"},
		{errors.New("Error: invalid JSON"), "Error: invalid JSON"},
	} {
		m := testModel()
		m.loading = true
		m, _ = update(m, searchResultMsg{id: m.requestID, query: "video", err: test.err})
		if m.loading || m.showingResults || !m.statusError || !m.input.Focused() || !strings.Contains(ansi.Strip(m.View()), test.want) {
			t.Fatalf("status = %q, loading = %v, results = %v", m.status, m.loading, m.showingResults)
		}
	}
}

func TestSupersededAndClearedSearchesIgnoreLateResponses(t *testing.T) {
	m := press(testModel(), "first")
	m, _ = update(m, keyMsg("enter"))
	firstID := m.requestID
	m.input.SetValue("second")
	m, _ = update(m, keyMsg("enter"))
	secondID := m.requestID
	m, _ = update(m, searchResultMsg{id: firstID, packages: []Package{{Name: "stale"}}})
	if !m.loading || m.showingResults || !strings.Contains(m.status, "second") {
		t.Fatal("a superseded search must not replace a newer one")
	}
	m = press(m, "ctrl+l")
	m, _ = update(m, searchResultMsg{id: secondID, packages: []Package{{Name: "late"}}})
	if m.loading || m.showingResults || m.status != "" || m.cancelSearch != nil {
		t.Fatal("a late response must not resurrect cleared results")
	}
}

func TestResizeAndTableScrolling(t *testing.T) {
	m := testModel()
	packages := make([]Package, 7)
	for i := range packages {
		packages[i] = Package{Name: fmt.Sprintf("package%d", i), COPRDescription: strings.Repeat("界", 80), Reason: "reason"}
	}
	m, _ = update(m, searchResultMsg{id: m.requestID, packages: packages})
	m, _ = update(m, tea.WindowSizeMsg{Width: 50, Height: 10})
	m = press(m, "end")
	if m.rowCursor != 6 || !strings.Contains(m.table.View(), "package6") {
		t.Fatal("selected rows must scroll into view in short terminals")
	}
	m = press(m, "right")
	if m.tableOffset != 4 {
		t.Fatal("wide tables must scroll horizontally")
	}
	m = press(m, "left")
	m = press(m, "home")
	if m.tableOffset != 0 || m.rowCursor != 0 || m.table.YOffset != 0 {
		t.Fatal("scrolling back must restore the first row and column")
	}
	for _, size := range []tea.WindowSizeMsg{{Width: 120, Height: 30}, {Width: 50, Height: 10}, {Width: 20, Height: 8}, {Width: 1, Height: 1}} {
		m, _ = update(m, size)
		for _, key := range []string{"c", "end", "c", "ctrl+l"} {
			m = press(m, key)
			view := m.View()
			if lipgloss.Width(view) > size.Width || lipgloss.Height(view) > size.Height {
				t.Fatalf("view exceeds %dx%d: %dx%d", size.Width, size.Height, lipgloss.Width(view), lipgloss.Height(view))
			}
		}
		m = withResults(m)
	}
}

func TestClearCancelsRunningRequest(t *testing.T) {
	started := make(chan struct{})
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		close(started)
		<-r.Context().Done()
	}))
	defer server.Close()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	m := press(newModel(ctx, newSearchClient(server.URL)), "video")
	m, cmd := update(m, keyMsg("enter"))
	responses := make(chan searchResultMsg, 1)
	for _, command := range cmd().(tea.BatchMsg) {
		go func() {
			if result, ok := command().(searchResultMsg); ok {
				responses <- result
			}
		}()
	}
	select {
	case <-started:
	case <-time.After(2 * time.Second):
		t.Fatal("HTTP request did not start")
	}
	m = press(m, "ctrl+l")
	select {
	case response := <-responses:
		if !errors.Is(response.err, context.Canceled) {
			t.Fatalf("cleared request error = %v", response.err)
		}
		m, _ = update(m, response)
		if m.status != "" || m.loading || m.showingResults {
			t.Fatal("cancelled request changed the cleared view")
		}
	case <-time.After(2 * time.Second):
		t.Fatal("clearing did not cancel the HTTP request")
	}
}
