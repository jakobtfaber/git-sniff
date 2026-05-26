import sys
import json
import argparse
import asyncio
import logging

from rich.console import Console

from git_sniff.engine import evaluate, evaluate_detailed, parse_repo
from git_sniff.auth import resolve_token
from git_sniff.schemas import (
    GitSniffError, BadRepoError, RepoNotFoundError, RateLimitedError, EngineError
)

console = Console()
logging.basicConfig(level=logging.WARNING)


async def sniff_cli(repo: str):
    try:
        owner, repo_name = parse_repo(repo)
    except BadRepoError as e:
        console.print(f"[bold red]Error: {e}[/bold red]")
        sys.exit(2)

    error_text = None
    error_style = None
    with console.status(f"[bold blue]Sniffing repository {owner}/{repo_name}...[/bold blue]"):
        try:
            result = await evaluate_detailed(owner, repo_name, token=resolve_token())
        except RateLimitedError as e:
            error_text, error_style = str(e), "bold yellow"
        except (RepoNotFoundError, EngineError) as e:
            error_text, error_style = f"Error: {e}", "bold red"

    if error_text is not None:
        console.print(f"[{error_style}]{error_text}[/{error_style}]")
        sys.exit(1)

    card = result.scorecard
    desc = result.descriptions
    overall = card.overall_score
    scorecard_status = card.status
    recommendation = card.recommendation
    limit_warning = card.rate_limit_warning
    m_score = card.breakdown.maintenance
    c_score = card.breakdown.cicd
    d_score = card.breakdown.dependencies
    b_score = card.breakdown.bus_factor
    m_desc = desc["maintenance"]
    c_desc = desc["cicd"]
    d_desc = desc["dependencies"]
    b_desc = desc["bus_factor"]

    # Helper formatters
    def get_color_tag(score_val: int) -> str:
        if score_val >= 80:
            return "[green]🟢[/green]"
        elif score_val >= 50:
            return "[yellow]🟡[/yellow]"
        else:
            return "[red]🔴[/red]"

    def get_status_tag(status_val: str) -> str:
        if status_val == "HEALTHY":
            return "[bold green]HEALTHY[/bold green]"
        elif status_val == "WARNING":
            return "[bold yellow]WARNING[/bold yellow]"
        else:
            return "[bold red]CRITICAL[/bold red]"

    # Print Rich scorecard exactly matching SPEC Section 5.1 layout
    console.print("================================================================================", style="bold blue")
    console.print(f" GIT-SNIFF SCORECARD: {owner}/{repo_name}")
    console.print("================================================================================", style="bold blue")
    console.print(f" OVERALL SCORE: {overall}/100 [{get_color_tag(overall)} {get_status_tag(scorecard_status)}]")
    console.print("--------------------------------------------------------------------------------", style="blue")
    console.print(" 📊 METRIC BREAKDOWN:")
    console.print(f"  {get_color_tag(m_score)} Maintenance Vitality: {m_score}/100 ({m_desc})")
    console.print(f"  {get_color_tag(c_score)} CI/CD & Engineering Rigor: {c_score}/100 ({c_desc})")
    console.print(f"  {get_color_tag(d_score)} Dependency Hygiene: {d_score}/100 ({d_desc})")
    console.print(f"  {get_color_tag(b_score)} Bus Factor Sustainability: {b_score}/100 ({b_desc})")
    console.print("--------------------------------------------------------------------------------", style="blue")
    console.print(f" 💡 RECOMMENDATION: {recommendation}")
    
    if limit_warning:
        console.print("--------------------------------------------------------------------------------", style="yellow")
        console.print(f" {limit_warning}", style="bold yellow")
        
    console.print("================================================================================", style="bold blue")


async def sniff_json(repo: str) -> int:
    try:
        owner, repo_name = parse_repo(repo)
        scorecard = await evaluate(owner, repo_name, token=resolve_token())
    except GitSniffError as e:
        print(json.dumps({"error": str(e)}))
        return 1
    print(scorecard.model_dump_json())
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="git-sniff: Instant quality, architecture, and sustenance metrics scorecard for GitHub repositories."
    )
    parser.add_argument(
        "repository",
        nargs="?",
        help="The public GitHub repository formatted as owner/repo (e.g. langchain-ai/deepagents)"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the scorecard as JSON on stdout (for scripting); prints {\"error\": ...} and exits 1 on failure."
    )

    args = parser.parse_args()

    if args.repository and args.json:
        sys.exit(asyncio.run(sniff_json(args.repository)))
    elif args.repository:
        asyncio.run(sniff_cli(args.repository))
    else:
        parser.print_help()
        sys.exit(1)

if __name__ == "__main__":
    main()
