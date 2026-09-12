package main

import (
	"context"
	"fmt"
	"strings"

	"github.com/charmbracelet/bubbles/spinner"
	"github.com/charmbracelet/bubbles/textinput"
	"github.com/charmbracelet/bubbles/viewport"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
	"github.com/charmbracelet/x/ansi"
)

const searchLabel = "What do you need? Describe it in plain English:"
const drillbitASCII = `  ██████╗ ██████╗ ██╗██╗      ██╗      ██████╗ ██╗████████╗
  ██╔══██╗██╔══██╗██║██║      ██║      ██╔══██╗██║╚══██╔══╝
  ██║  ██║██████╔╝██║██║      ██║      ██████╔╝██║   ██║
  ██║  ██║██╔══██╗██║██║      ██║      ██╔══██╗██║   ██║
  ██████╔╝██║  ██╗██║███████╗ ███████╗ ██████╔╝██║   ██║
  ╚═════╝ ╚═╝  ╚═╝╚═╝╚══════╝ ╚══════╝ ╚═════╝ ╚═╝   ╚═╝`

type searchResultMsg struct {
	id       int
	query    string
	packages []Package
	err      error
}

type model struct {
	ctx            context.Context
	client         searchClient
	input          textinput.Model
	spinner        spinner.Model
	table          viewport.Model
	columns        []column
	packages       []Package
	width          int
	height         int
	rowCursor      int
	columnCursor   int
	tableOffset    int
	tableHeader    string
	resultsHeader  string
	status         string
	statusError    bool
	loading        bool
	showingResults bool
	columnsOpen    bool
	requestID      int
	cancelSearch   context.CancelFunc
}

func newModel(ctx context.Context, client searchClient) model {
	input := textinput.New()
	input.Prompt = ""
	input.Placeholder = `e.g. "a tool for editing video files" or "screen recorder"`
	input.Focus()
	m := model{
		ctx: ctx, client: client, input: input,
		spinner: spinner.New(spinner.WithSpinner(spinner.Dot)),
		table:   viewport.New(1, 1), columns: defaultColumns(),
		width: 80, height: 24,
	}
	m.resize()
	return m
}

func (m model) Init() tea.Cmd {
	return textinput.Blink
}

func (m model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.width, m.height = max(1, msg.Width), max(1, msg.Height)
		m.resize()
		return m, nil
	case searchResultMsg:
		if msg.id != m.requestID {
			return m, nil
		}
		m.loading = false
		m.cancelSearch = nil
		if msg.err != nil {
			m.status, m.statusError = msg.err.Error(), true
		} else if len(msg.packages) == 0 {
			m.status = fmt.Sprintf("No packages found for \"%s\". Try a different description.", msg.query)
			m.statusError = true
		} else {
			m.packages = msg.packages
			m.showingResults = true
			m.input.Blur()
			m.rowCursor, m.tableOffset = 0, 0
			plural := "s"
			if len(msg.packages) == 1 {
				plural = ""
			}
			m.resultsHeader = fmt.Sprintf("Found %d package%s for \"%s\"  —  c: columns  |  ctrl+l: new search", len(msg.packages), plural, msg.query)
			m.resize()
		}
		return m, nil
	case spinner.TickMsg:
		if m.loading {
			var cmd tea.Cmd
			m.spinner, cmd = m.spinner.Update(msg)
			return m, cmd
		}
		return m, nil
	case tea.KeyMsg:
		switch msg.String() {
		case "ctrl+q":
			m.stopSearch()
			return m, tea.Quit
		case "ctrl+l":
			cmd := m.clearResults()
			return m, cmd
		case "f1":
			if m.showingResults {
				cmd := m.clearResults()
				return m, cmd
			}
			cmd := m.input.Focus()
			return m, cmd
		case "esc":
			if m.columnsOpen {
				m.columnsOpen = false
				m.resize()
			} else if m.showingResults {
				cmd := m.clearResults()
				return m, cmd
			} else {
				m.input.Blur()
			}
			return m, nil
		case "tab", "shift+tab":
			if !m.showingResults {
				cmd := m.input.Focus()
				return m, cmd
			}
		case "c":
			if m.showingResults {
				m.columnsOpen = !m.columnsOpen
				m.resize()
				return m, nil
			}
		case "enter":
			if !m.showingResults && m.input.Focused() {
				query := strings.TrimSpace(m.input.Value())
				if query != "" {
					cmd := m.startSearch(query)
					return m, cmd
				}
				return m, nil
			}
		}
		if m.showingResults {
			m.updateResultsKey(msg.String())
			return m, nil
		}
	}
	var cmd tea.Cmd
	m.input, cmd = m.input.Update(msg)
	return m, cmd
}

func (m *model) stopSearch() {
	if m.cancelSearch != nil {
		m.cancelSearch()
		m.cancelSearch = nil
	}
	// Results from cancelled or superseded requests must not change the view.
	m.requestID++
	m.loading = false
}

func (m *model) startSearch(query string) tea.Cmd {
	m.stopSearch()
	ctx, cancel := context.WithCancel(m.ctx)
	m.cancelSearch = cancel
	m.loading = true
	m.status = fmt.Sprintf("Drilling into packages for: \"%s\"...", query)
	m.statusError = false
	id, client := m.requestID, m.client
	request := func() tea.Msg {
		defer cancel()
		packages, err := client.search(ctx, query)
		return searchResultMsg{id: id, query: query, packages: packages, err: err}
	}
	return tea.Batch(request, m.spinner.Tick)
}

func (m *model) clearResults() tea.Cmd {
	m.stopSearch()
	m.packages = nil
	m.showingResults, m.columnsOpen = false, false
	m.status, m.resultsHeader = "", ""
	m.statusError = false
	m.rowCursor, m.tableOffset = 0, 0
	m.input.Reset()
	m.resize()
	return m.input.Focus()
}

func (m *model) updateResultsKey(key string) {
	if m.columnsOpen {
		switch key {
		case "up":
			m.columnCursor = max(0, m.columnCursor-1)
		case "down":
			m.columnCursor = min(len(m.columns)-1, m.columnCursor+1)
		case "home":
			m.columnCursor = 0
		case "end":
			m.columnCursor = len(m.columns) - 1
		case " ", "enter":
			m.columns[m.columnCursor].selected = !m.columns[m.columnCursor].selected
			m.rowCursor, m.tableOffset = 0, 0
		}
	} else {
		switch key {
		case "up":
			m.rowCursor--
		case "down":
			m.rowCursor++
		case "pgup":
			m.rowCursor -= m.table.Height
		case "pgdown":
			m.rowCursor += m.table.Height
		case "home":
			m.rowCursor = 0
		case "end":
			m.rowCursor = len(m.packages) - 1
		case "left":
			m.tableOffset -= 4
		case "right":
			m.tableOffset += 4
		}
		m.rowCursor = min(max(0, m.rowCursor), max(0, len(m.packages)-1))
	}
	m.rebuildTable()
}

func (m model) headerView() string {
	if m.width < lipgloss.Width(drillbitASCII) || m.height < 18 {
		return lipgloss.PlaceHorizontal(m.width, lipgloss.Center, "Drillbit")
	}
	return "\n" + lipgloss.PlaceHorizontal(m.width, lipgloss.Center, drillbitASCII) + "\n"
}

func (m model) bodyHeight() int {
	return max(1, m.height-lipgloss.Height(m.headerView())-1)
}

func (m model) pickerWidth() int {
	if !m.columnsOpen {
		return 0
	}
	return min(26, max(1, m.width/2))
}

func (m *model) resize() {
	m.input.Width = max(1, m.width*7/10-6)
	m.table.Width = max(1, m.width-4-m.pickerWidth())
	m.table.Height = max(1, m.bodyHeight()-lipgloss.Height(m.resultsHeaderView())-2)
	m.rebuildTable()
}

func (m model) resultsHeaderView() string {
	return lipgloss.NewStyle().Foreground(lipgloss.Color("2")).Width(m.table.Width).
		Render(singleLine(m.resultsHeader))
}

func (m model) View() string {
	header := m.headerView()
	height := m.bodyHeight()
	var body string
	if m.showingResults {
		columnHeader := ansi.Cut(m.tableHeader, m.tableOffset, m.tableOffset+m.table.Width)
		body = lipgloss.NewStyle().Padding(0, 2).Width(m.table.Width + 4).Render(
			m.resultsHeaderView() + "\n\n" + columnHeader + "\n" + m.table.View())
		if m.columnsOpen {
			body = lipgloss.JoinHorizontal(lipgloss.Top, body, m.pickerView(m.pickerWidth(), height))
		}
	} else {
		borderColor := lipgloss.Color("8")
		if m.input.Focused() {
			borderColor = lipgloss.Color("7")
		}
		input := lipgloss.NewStyle().Border(lipgloss.NormalBorder()).BorderForeground(borderColor).
			Padding(0, 2).Render(m.input.View())
		statusStyle := lipgloss.NewStyle().Width(max(1, m.width-4)).Align(lipgloss.Center)
		if m.statusError {
			statusStyle = statusStyle.Foreground(lipgloss.Color("1"))
		}
		parts := []string{searchLabel, "", input, "", statusStyle.Render(singleLine(m.status))}
		if m.loading {
			parts = append(parts, m.spinner.View()+" Searching…")
		}
		body = lipgloss.JoinVertical(lipgloss.Center, parts...)
		body = lipgloss.Place(m.width, height, lipgloss.Center, lipgloss.Center, body)
	}
	body = lipgloss.NewStyle().Width(m.width).Height(height).MaxWidth(m.width).MaxHeight(height).Render(body)
	footer := ansi.Truncate("ctrl+q Quit  ctrl+l Clear  c Columns  f1 Search", m.width, "")
	return lipgloss.NewStyle().MaxWidth(m.width).MaxHeight(m.height).Render(header + "\n" + body + "\n" + footer)
}
