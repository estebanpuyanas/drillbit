// Drillbit is a host-side terminal client for the package search backend.
package main

import (
	"context"
	"fmt"
	"os"

	tea "github.com/charmbracelet/bubbletea"
)

func main() {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	backendURL := os.Getenv("BACKEND_URL")
	if backendURL == "" {
		backendURL = "http://localhost:8000"
	}
	program := tea.NewProgram(newModel(ctx, newSearchClient(backendURL)), tea.WithAltScreen())
	if _, err := program.Run(); err != nil {
		fmt.Fprintln(os.Stderr, "Drillbit:", err)
		os.Exit(1)
	}
}
