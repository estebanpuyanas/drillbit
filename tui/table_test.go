package main

import (
	"strings"
	"testing"
	"time"

	"github.com/charmbracelet/x/ansi"
)

func TestCellFormatting(t *testing.T) {
	pkg := Package{
		Name: "editor", COPRDescription: strings.Repeat("界", 81),
		Reason: strings.Repeat("é", 80), Version: "2.0", COPRProject: "owner/editor",
		SubmittedOn: float64(1700000000), EndedOn: "1700001000",
	}
	for _, test := range []struct{ key, want string }{
		{"name", "editor"}, {"copr_description", strings.Repeat("界", 80) + "…"},
		{"reason", strings.Repeat("é", 80)}, {"version", "2.0"},
		{"copr_project", "owner/editor"},
		{"submitted_on", time.Unix(1700000000, 0).In(time.Local).Format("2006-01-02")},
		{"ended_on", time.Unix(1700001000, 0).In(time.Local).Format("2006-01-02")},
	} {
		if got := renderCell(test.key, pkg); got != test.want {
			t.Errorf("%s = %q; want %q", test.key, got, test.want)
		}
	}
	for _, col := range defaultColumns() {
		if got := renderCell(col.key, Package{}); got != "N/A" {
			t.Errorf("missing %s = %q; want N/A", col.key, got)
		}
	}
}

func TestDateFormatting(t *testing.T) {
	for _, value := range []any{nil, "", "invalid", "1.5", float64(0), float64(1e30), "9999999999999999", true} {
		if got := renderDate(value); got != "N/A" {
			t.Errorf("invalid date %v = %q", value, got)
		}
	}
	for _, value := range []any{"1700000000", float64(1700000000.9)} {
		want := time.Unix(1700000000, 0).In(time.Local).Format("2006-01-02")
		if got := renderDate(value); got != want {
			t.Errorf("date %v = %q; want local date %q", value, got, want)
		}
	}
}

func TestTableDisplaysMissingFieldsAndPlainText(t *testing.T) {
	m := testModel()
	m.packages = []Package{{Name: "first", Reason: "[bold]literal[/bold]"}, {Name: "second", COPRDescription: "text\nnext\x1b[31mred\x1b[0m"}}
	m.rebuildTable()
	view := ansi.Strip(m.table.View())
	if !strings.Contains(view, "N/A") || !strings.Contains(view, "[bold]literal[/bold]") || !strings.Contains(view, "text nextred") {
		t.Fatalf("unexpected table cells:\n%s", view)
	}
}
