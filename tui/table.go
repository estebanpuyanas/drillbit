package main

import (
	"fmt"
	"math"
	"strconv"
	"strings"
	"time"
	"unicode"

	"github.com/charmbracelet/lipgloss"
	"github.com/charmbracelet/x/ansi"
)

type column struct {
	key      string
	label    string
	selected bool
}

func defaultColumns() []column {
	return []column{
		{"name", "Package", true},
		{"copr_description", "Description", true},
		{"reason", "Reason", true},
		{"version", "Version", false},
		{"submitted_on", "Submitted", false},
		{"ended_on", "Last Built", false},
		{"copr_project", "COPR Project", false},
	}
}

func (m model) visibleColumns() []column {
	var visible []column
	for _, col := range m.columns {
		if col.selected {
			visible = append(visible, col)
		}
	}
	return visible
}

func renderCell(key string, pkg Package) string {
	var value string
	switch key {
	case "name":
		value = pkg.Name
	case "copr_description":
		value = pkg.COPRDescription
	case "reason":
		value = pkg.Reason
	case "version":
		value = pkg.Version
	case "submitted_on":
		return renderDate(pkg.SubmittedOn)
	case "ended_on":
		return renderDate(pkg.EndedOn)
	case "copr_project":
		value = pkg.COPRProject
	}
	if value == "" {
		return "N/A"
	}
	if key == "copr_description" || key == "reason" {
		runes := []rune(value)
		if len(runes) > 80 {
			value = string(runes[:80]) + "…"
		}
	}
	return singleLine(value)
}

func renderDate(value any) string {
	var seconds int64
	switch value := value.(type) {
	case float64:
		if value == 0 || math.IsNaN(value) || math.IsInf(value, 0) || value >= math.MaxInt64 || value < math.MinInt64 {
			return "N/A"
		}
		seconds = int64(value)
	case string:
		parsed, err := strconv.ParseInt(strings.TrimSpace(value), 10, 64)
		if err != nil {
			return "N/A"
		}
		seconds = parsed
	default:
		return "N/A"
	}
	date := time.Unix(seconds, 0).In(time.Local)
	if date.Year() < 1 || date.Year() > 9999 {
		return "N/A"
	}
	return date.Format("2006-01-02")
}

// Keep backend text in a single table row and prevent terminal control sequences
// in package metadata from becoming terminal commands.
func singleLine(value string) string {
	return strings.Map(func(r rune) rune {
		if unicode.IsControl(r) {
			return ' '
		}
		return r
	}, ansi.Strip(value))
}

// Natural column widths preserve the complete cell values. The viewport scrolls
// horizontally when the selected columns do not fit in the terminal.
func (m *model) rebuildTable() {
	visible := m.visibleColumns()
	widths := make([]int, len(visible))
	rows := make([][]string, len(m.packages))
	for i, col := range visible {
		widths[i] = lipgloss.Width(col.label)
	}
	for i, pkg := range m.packages {
		rows[i] = make([]string, len(visible))
		for j, col := range visible {
			rows[i][j] = renderCell(col.key, pkg)
			widths[j] = max(widths[j], lipgloss.Width(rows[i][j]))
		}
	}
	var header strings.Builder
	for i, col := range visible {
		header.WriteString(lipgloss.NewStyle().Bold(true).Padding(0, 1).Width(widths[i] + 2).Render(col.label))
	}
	m.tableHeader = header.String()
	var lines []string
	for i, row := range rows {
		var line strings.Builder
		for j, value := range row {
			style := lipgloss.NewStyle().Padding(0, 1).Width(widths[j] + 2)
			if i == 0 && visible[j].key == "name" {
				style = style.Bold(true)
			}
			if i == m.rowCursor && !m.columnsOpen {
				style = style.Reverse(true)
			}
			line.WriteString(style.Render(value))
		}
		lines = append(lines, line.String())
	}
	m.table.SetContent(strings.Join(lines, "\n"))
	m.tableOffset = min(max(0, m.tableOffset), max(0, lipgloss.Width(m.tableHeader)-m.table.Width))
	m.table.SetXOffset(m.tableOffset)
	if m.rowCursor < m.table.YOffset {
		m.table.SetYOffset(m.rowCursor)
	} else if m.rowCursor >= m.table.YOffset+m.table.Height {
		m.table.SetYOffset(m.rowCursor - m.table.Height + 1)
	}
	m.table.SetYOffset(m.table.YOffset)
}

func (m model) pickerView(width, height int) string {
	lines := []string{"Columns", ""}
	for i, col := range m.columns {
		marker, checked := " ", " "
		if i == m.columnCursor {
			marker = ">"
		}
		if col.selected {
			checked = "x"
		}
		lines = append(lines, fmt.Sprintf("%s [%s] %s", marker, checked, col.label))
	}
	lines = append(lines, "", "↑/↓: move  space: toggle")
	// Keep the selected option reachable even in a short terminal.
	start := max(0, m.columnCursor+3-height)
	end := min(len(lines), start+height)
	return lipgloss.NewStyle().Width(width).MaxWidth(width).Height(height).MaxHeight(height).
		Render(strings.Join(lines[start:end], "\n"))
}
