"""Human-in-the-Loop (HIL) manager for tool execution confirmations.

This module manages HIL confirmations for tool calls, allowing users to review,
approve, or skip tool executions before they are performed.
"""

from rich.prompt import Prompt
from rich.console import Console
from typing import Optional, Tuple
import re

class AbortQueryException(Exception):
    """Exception raised when user chooses to abort the current query.

    This signals that the query should be stopped and not saved to history.
    """
    pass


class HumanInTheLoopManager:
    """Manages Human-in-the-Loop confirmations for tool execution"""

    def __init__(self, console: Console):
        """Initialize the HIL manager.

        Args:
            console: Rich console for output
        """
        self.console = console
        # Store HIL settings locally since there's no persistent config object
        self._hil_enabled = True  # Default to enabled
        # Per-query/session option to auto-execute tools without asking for
        # the remainder of the current model/query process. This is not
        # persisted and resets between queries.
        self._session_auto_execute = False

    def is_enabled(self) -> bool:
        """Check if HIL confirmations are enabled"""
        return self._hil_enabled

    def toggle(self) -> None:
        """Toggle HIL confirmations"""
        if self.is_enabled():
            self.set_enabled(False)
        else:
            self.set_enabled(True)
            self.console.print("[green]🧑‍💻 HIL confirmations enabled[/green]")
            self.console.print("[dim]You will be prompted to confirm each tool call.[/dim]")

    def set_enabled(self, enabled: bool) -> None:
        """Set HIL enabled state (used when loading from config)"""
        self._hil_enabled = enabled

    def set_session_auto_execute(self, enabled: bool) -> None:
        """Enable or disable session-level auto-execution.

        When enabled, tool confirmations will be skipped for the remainder
        of the current query/process session. This is not persisted.
        """
        self._session_auto_execute = enabled

    def reset_session(self) -> None:
        """Reset any per-query/session HIL state.

        Call this between model/query process loops to ensure session
        options don't leak into subsequent queries.
        """
        self._session_auto_execute = False

    async def request_tool_confirmation(self, tool_name: str, tool_args: dict, return_rejection_reason: bool = False) -> Tuple[bool, Optional[str]]:
        """
        Request user confirmation for tool execution

        Args:
            tool_name: Name of the tool to execute
            tool_args: Arguments for the tool
            return_rejection_reason: Whether to ask for and return a rejection reason

        Returns:
            tuple: (should_execute, rejection_reason_or_none)
                   should_execute is a bool, rejection_reason is str or None
        """
        if not self.is_enabled():
            return (True, None)  # Execute if HIL is disabled

        # If the session-level auto-execute has been enabled earlier in
        # this query/process, skip prompting and execute automatically.
        if self._session_auto_execute:
            return (True, None)

        self.console.print("\n[bold yellow]🧑‍💻 Human-in-the-Loop Confirmation[/bold yellow]")

        # Show tool information
        self.console.print(f"[cyan]Tool to execute:[/cyan] [bold]{tool_name}[/bold]")

        # Show arguments
        if tool_args:
            self.console.print("[cyan]Arguments:[/cyan]")
            for key, value in tool_args.items():
                # Truncate long values for display
                display_value = str(value)
                if len(display_value) > 50:
                    display_value = display_value[:47] + "..."
                self.console.print(f"  • {key}: {display_value}")
        else:
            self.console.print("[cyan]Arguments:[/cyan] [dim]None[/dim]")

        self.console.print()

        # Display options
        self._display_confirmation_options()

        choice = Prompt.ask(
            "[bold]What would you like to do?[/bold]",
            choices=["y", "yes", "n", "no", "s", "session", "d", "disable", "a", "abort"],
            default="y",
            show_choices=False
        ).lower()

        return self._handle_user_choice(choice)

    def _display_confirmation_options(self) -> None:
        """Display available confirmation options"""
        self.console.print("[bold cyan]Options:[/bold cyan]")
        self.console.print("  [green]y/yes[/green] - Execute the tool call")
        self.console.print("  [red]n/no[/red] - Skip this tool call")
        self.console.print("  [magenta]s/session[/magenta] - Execute without asking for this session")
        self.console.print("  [yellow]d/disable[/yellow] - Disable HIL confirmations permanently")
        self.console.print("  [bold red]a/abort[/bold red] - Abort this query (won't save to history)")
        self.console.print()


    def _handle_user_choice(self, choice: str, tool_name: str = None, return_rejection_reason: bool = False) -> Tuple[bool, Optional[str]]:
        """
        Handle user's confirmation choice

        Args:
            choice: User's choice string
            tool_name: Name of the tool being confirmed (for tool approval)
            return_rejection_reason: If True and user rejects, also return the reason

        Returns:
            tuple: (should_execute, rejection_reason_or_none)
                   should_execute is a bool, rejection_reason is str or None
        """

        if choice in ["d", "disable"]:
            # Notify user that it can be re-enabled
            self.console.print("\n[yellow]Tool calls will proceed automatically without confirmation.[/yellow]")
            self.console.print("[cyan]You can re-enable this with the command: human-in-loop or hil[/cyan]\n")

            # Ask for confirmation to disable permanently
            execute_current = Prompt.ask(
                "[bold]Are you sure you want to disable HIL confirmations permanently?[/bold]",
                choices=["y", "yes", "n", "no"],
                default="y"
            ).lower()

            should_execute = execute_current in ["y", "yes"]
            if should_execute:
                self.toggle()  # Disable HIL
                self.console.print("[yellow]🤖 HIL confirmations disabled[/yellow]")
            return (should_execute, None)

        elif choice in ["s", "session"]:
            self.set_session_auto_execute(True)
            self.console.print("[magenta]🧑‍💻 Tool calls will proceed automatically for the remainder of this session.[/magenta]")
            return True

        elif choice in ["a", "abort"]:
            self.console.print("[bold red]🛑 Aborting query...[/bold red]")
            raise AbortQueryException("Query aborted by user during tool confirmation")

        elif choice in ["n", "no"]:
            # Always ask for rejection reason when user selects 'n'/'no'
            self.console.print(f"\n[bold cyan]🔍 Rejecting tool: {tool_name}[/bold cyan]")
            rejection_reason = self._ask_for_rejection_reason()
            tool_response = f"Tool call was skipped by user. Reason: {rejection_reason}"
            
            self.console.print(f"[yellow]⏭️  Tool call skipped[/yellow]")
            self.console.print(f"[dim]Reason for skipping: {rejection_reason}[/dim]")
            self.console.print("[dim]Tip: Use 'human-in-loop' or 'hil' to disable these confirmations permanently[/dim]")
            return (False, rejection_reason)

        else:  # y/yes
            self.console.print("[dim]Tip: Use 'human-in-loop' or 'hil' to disable these confirmations[/dim]")
            return (True, None)
    
    # Note: _show_other_menu was removed - o/other option is no longer available

    def _ask_for_rejection_reason(self, tool_name: str = None) -> str:
        """Ask the user for a reason when rejecting a tool call.
        
        Provides both preset options and free-text input for flexible rejection reasons.
        
        Args:
            tool_name: Name of the tool being rejected (for display purposes)
            
        Returns:
            str: The rejection reason provided by the user
        """
        self.console.print()
        self.console.print("[bold yellow]❓ Please provide a reason for rejecting this tool call:[/bold yellow]")
        self.console.print()
        
        # Common rejection reasons as preset options
        common_reasons = [
            "Wrong tool",
            "Timing not right", 
            "Don't need that info",
            "Already have this",
            "Security concern",
            "Privacy concern",
            "Tool not trusted",
            "Other reason"
        ]
        
        self.console.print("[bold cyan]Common reasons:[/bold cyan]")
        for idx, reason in enumerate(common_reasons, start=1):
            self.console.print(f"  [{idx}]. {reason}")
        self.console.print()
        
        # Get user input - allow direct free text or select from preset options
        self.console.print()
        self.console.print("[bold cyan]Enter your reason or select a preset (1-8):[/bold cyan]")
        
        # Get user input without choices restriction - accept any text input
        choice = Prompt.ask(
            "[bold]Reason:[/bold]",
            default=""
        ).strip()
        
        if choice.isdigit() and int(choice) in range(1, 9):
            selected_idx = int(choice) - 1
            if selected_idx < len(common_reasons):
                reason = common_reasons[selected_idx]
                # If user selected "Other reason", prompt for custom input
                if selected_idx == len(common_reasons) - 1:  # "Other reason"
                    self.console.print()
                    custom_reason = Prompt.ask(
                        "[bold]Enter your own reason:[/bold]",
                        default=""
                    ).strip()
                    if custom_reason:
                        return custom_reason
                    else:
                        # If no custom reason provided, use the "Other reason" placeholder
                        return f"Other reason (not specified)"
                else:
                    return reason
            else:
                # Fallback to default
                return "Other reason (not specified)"
        else:
            # User typed a free-form reason directly or empty input
            if choice:
                return choice
            else:
                return "User skipped reason input"
    
    def _show_tool_approval_menu(self, current_tool_name: str = None) -> None:
        """Show the tool approval management menu.
        
        Args:
            current_tool_name: Optional name of the currently being confirmed tool
                              (used for quick approval of this tool)
        """
        if not self._tool_approval_manager:
            self.console.print("[yellow]⚠️  Tool approval manager not initialized[/yellow]")
            return

        while True:
            self.console.print()
            self.console.print("[bold cyan]🧑‍💻 Tool Approval Manager[/bold cyan]")
            self.console.print("=" * 50)
            
            # Show current approvals
            approvals = self._tool_approval_manager.get_all_approvals()
            if approvals:
                self.console.print("\n[bold]Active Tool Approvals:[/bold]\n")
                for tool_or_group, count in sorted(approvals.items()):
                    style = "green" if count > 5 else "yellow"
                    self.console.print(f"  [{style}]• {tool_or_group}: [bold]{count}[/bold] remaining[/]")
            else:
                self.console.print("\n[dim]No active tool approvals.[/dim]")
            
            self.console.print()
            self.console.print("[bold cyan]Options:[/bold cyan]")
            
            # Show quick approval for current tool if available
            if current_tool_name:
                group = self._tool_approval_manager._get_tool_group(current_tool_name)
                self.console.print(f"  [green][1][/green] - Approve current tool ([bold]{current_tool_name}[/bold]) for 1 use")
                self.console.print(f"  [green][2][/green] - Approve current tool group ([bold]{group}.*[/bold]) for 1 use")
            
            self.console.print("  [green][3][/green] - Approve a specific tool with count")
            self.console.print("  [green][4][/green] - Approve a tool group with count (e.g., 'filesystem.*')")
            self.console.print("  [red][5][/red] - Remove approval for a specific tool")
            self.console.print("  [red][6][/red] - Remove approval for a tool group")
            self.console.print("  [red][7][/red] - Clear all approvals")
            self.console.print("  [cyan][0][/cyan] - Back to tool confirmation")
            self.console.print()

            choice = Prompt.ask(
                "Choose an option",
                choices=["1", "2", "3", "4", "5", "6", "7", "0"],
                default="0"
            ).strip()

            if choice == "0":
                break
            
            elif choice == "7":
                # Clear all approvals
                self.console.print("[yellow]Clearing all tool approvals...[/yellow]")
                self._tool_approval_manager.clear_all()
                self.console.print("[green]✅ All approvals cleared[/green]")

            elif choice in ["5", "6"]:
                # Remove approval
                remove_type = "tool" if choice == "5" else "group"
                is_group = (choice == "6")
                
                if is_group:
                    tool_name = Prompt.ask(
                        "[bold]Enter group name to remove approval for (e.g., 'filesystem')[/bold]",
                        default=""
                    ).strip()
                    if tool_name:
                        self._tool_approval_manager.remove_approval(f"{tool_name}.*")
                        self.console.print(f"[green]✅ Removed approval for group '{tool_name}'[/green]")
                else:
                    tool_name = Prompt.ask(
                        "[bold]Enter tool name to remove approval for[/bold]",
                        default=""
                    ).strip()
                    if tool_name:
                        self._tool_approval_manager.remove_approval(tool_name)
                        self.console.print(f"[green]✅ Removed approval for tool '{tool_name}'[/green]")

            elif choice in ["3", "4"]:
                # Add new approval
                is_group = (choice == "4")
                
                if is_group:
                    target = Prompt.ask(
                        "[bold]Enter group name to approve (e.g., 'filesystem') - will apply to {group}.*[/bold]",
                        default=""
                    ).strip()
                    if not target:
                        continue
                    full_target = f"{target}.*"
                else:
                    target = Prompt.ask(
                        "[bold]Enter tool name to approve[/bold]",
                        default=""
                    ).strip()
                    if not target:
                        continue
                    full_target = target

                # Explain the input formats
                self.console.print()
                self.console.print("[dim]Supported formats: '10' or 'y10' for single tools, 'S10' for groups[/dim]")
                
                count_input = Prompt.ask(
                    f"[bold]How many times should '{full_target}' be approved? (e.g., 10, y10, or S10)[/bold]",
                    default="1"
                ).strip()
                
                if not count_input:
                    continue
                
                # Parse the input manually since we're accepting different formats
                try:
                    parsed = self._tool_approval_manager.parse_approval_input(count_input)
                    parsed_input, count = parsed
                    
                    # Determine type based on prefix
                    is_group_input = parsed_input.startswith('S')
                    
                    # If input was meant for group but target is not a group pattern, adjust
                    if is_group_input and not full_target.endswith('.*'):
                        self.console.print("[yellow]⚠️  'S' prefix is for group approvals only. Using single tool format instead.[/yellow]")
                    
                    # Add approval based on input type
                    if parsed_input.startswith('S') or (is_group_input and not full_target.endswith('.*')):
                        # Group approval format but target might be single - use appropriate pattern
                        group = self._tool_approval_manager._get_tool_group(target) if '.' in target else target
                        full_pattern = f"{group}.*" if is_group or parsed_input.startswith('S') else full_target
                        self._tool_approval_manager.add_approval(full_pattern, count)
                    else:
                        # Single tool approval (no prefix or 'y' prefix)
                        self._tool_approval_manager.add_approval(full_target, count)
                    
                    self.console.print(f"[green]✅ Approved '{full_target}' for {count} execution(s)[/green]")
                except ValueError as e:
                    self.console.print(f"[yellow]⚠️  Invalid input: {str(e)}[/yellow]")

            elif choice == "1" and current_tool_name:
                # Quick approve current tool only
                if current_tool_name:
                    self._tool_approval_manager.add_approval(current_tool_name, 1)
                    self.console.print(f"[green]✅ Approved '{current_tool_name}' for 1 execution[/green]")

            elif choice == "2" and current_tool_name:
                # Quick approve current tool's group
                if current_tool_name:
                    group = self._tool_approval_manager._get_tool_group(current_tool_name)
                    self._tool_approval_manager.add_approval(f"{group}.*", 1)
                    self.console.print(f"[green]✅ Approved entire group '{group}.*' for 1 execution[/green]")

    # Removed _handle_other_menu_choice - the o/other option is no longer available
