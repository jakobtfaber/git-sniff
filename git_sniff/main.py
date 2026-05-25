import sys
import os
import argparse
import asyncio
import logging
from typing import Optional

import uvicorn
from rich.console import Console

from git_sniff.client import GitHubClient
from git_sniff.metrics import (
    calculate_maintenance_score,
    calculate_cicd_score,
    calculate_dependency_score,
    calculate_bus_factor_score,
    calculate_overall_score
)

console = Console()
logging.basicConfig(level=logging.WARNING)

async def sniff_cli(repo: str):
    """
    CLI Execution Engine: retrieves stats, computes scores, and outputs a colored scorecard.
    """
    if "/" not in repo or len(repo.split("/")) != 2:
        console.print("[bold red]Error: Invalid repository format. Please use 'owner/repo' (e.g. langchain-ai/deepagents)[/bold red]")
        sys.exit(1)
    
    owner, repo_name = repo.split("/")
    token = os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN")
    
    with console.status(f"[bold blue]Sniffing repository {owner}/{repo_name}...[/bold blue]"):
        try:
            async with GitHubClient(token=token) as client:
                # 1. Fetch Repository Metadata
                meta = await client.fetch_repo_metadata(owner, repo_name)
                default_branch = meta.get("default_branch", "main")
                stars = meta.get("stargazers_count", 0)
                open_issues = meta.get("open_issues_count", 0)

                # Fetch ingestion tasks concurrently using asyncio.gather
                issues_task = client.fetch_issues(owner, repo_name)
                tree_task = client.fetch_file_tree(owner, repo_name, default_branch)
                status_task = client.fetch_commit_status(owner, repo_name, default_branch)
                commits_task = client.fetch_commits(owner, repo_name, per_page=50)
                contribs_task = client.fetch_contributors(owner, repo_name)

                issues, file_paths, status, commits, contributors = await asyncio.gather(
                    issues_task, tree_task, status_task, commits_task, contribs_task
                )

                # Fallback for contributor stats if compilation timed out or failed
                if not contributors:
                    fallback_commits = await client.fetch_commits(owner, repo_name, per_page=100)
                    author_commits = {}
                    for c in fallback_commits:
                        login = (c.get("author") or {}).get("login")
                        name_info = (c.get("commit") or {}).get("author") or {}
                        identifier = login or name_info.get("name") or "unknown"
                        author_commits[identifier] = author_commits.get(identifier, 0) + 1
                    
                    contributors = [{"login": k, "contributions": v} for k, v in author_commits.items()]

                # Calculate dependencies count
                deps_count, pyproject_linting = await client.calculate_dependencies_count(
                    owner, repo_name, default_branch, file_paths
                )

                # Calculate Pillars
                m_score, m_desc = calculate_maintenance_score(issues, stars, open_issues)
                c_score, c_desc = calculate_cicd_score(file_paths, status, pyproject_linting)
                d_score, d_desc = calculate_dependency_score(commits, deps_count, file_paths)
                b_score, b_desc = calculate_bus_factor_score(contributors)

                # Calculate Overall Scorecard
                overall, scorecard_status, recommendation = calculate_overall_score(
                    maintenance=m_score,
                    cicd=c_score,
                    dependencies=d_score,
                    bus_factor=b_score
                )
                
                # Check for rate limits
                limit_warning = client.get_rate_limit_warning()
        
        except ValueError as e:
            console.print(f"[bold red]Error: {str(e)}[/bold red]")
            sys.exit(1)
        except Exception as e:
            console.print(f"[bold red]Unexpected Ingestion Error: {str(e)}[/bold red]")
            sys.exit(1)

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
        "--server",
        action="store_true",
        help="Start the background microservice API server"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to run the API server on (default: 8000)"
    )

    args = parser.parse_args()

    if args.server:
        console.print(f"[bold green]Starting git-sniff microservice on http://127.0.0.1:{args.port}...[/bold green]")
        uvicorn.run("git_sniff.server:app", host="127.0.0.1", port=args.port, log_level="info")
    elif args.repository:
        asyncio.run(sniff_cli(args.repository))
    else:
        parser.print_help()
        sys.exit(1)

if __name__ == "__main__":
    main()
