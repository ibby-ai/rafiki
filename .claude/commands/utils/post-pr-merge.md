---
allowed-tools: Bash(git add:*), Bash(git status:*), Bash(git commit:*), Bash(git push:*)
description: Post PR Merge
---

# Post PR merge tasks

## Context

We have a PR that has been merged into the main branch and the remote feature branch has been deleted. And the current local feature branch $BRANCH_NAME is now ready to be deleted.

## Your task

Please delete the local feature branch $BRANCH_NAME. If BRANCH_NAME is not provided, assume the current feature branch is the branch.
