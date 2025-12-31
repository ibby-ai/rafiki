---
allowed-tools: Bash(git tag:*), Bash(git push origin:*), Bash(gh release:*), Bash(git log:*)
description: Create a new git tag and draft GitHub release
---

## Context

- Existing tags: !`git tag -l --sort=-v:refname`
- Existing releases: !`gh release list --limit 5`
- Current branch: !`git branch --show-current`
- Current HEAD: !`git log --oneline -1`
- Commits since last tag: (determine by running `git log <latest-tag>..HEAD --oneline` using the latest tag from above)

## Your task

Based on the above context:

1. **Determine the next version** by incrementing from the latest tag (follow semver: vX.Y.Z)
2. **Create an annotated tag** at HEAD with the new version
3. **Push the tag** to origin
4. **Create a draft GitHub release** with release notes that summarize:
   - New features (from `feat:` commits)
   - Improvements (from `refactor:`, `perf:` commits)
   - Fixes (from `fix:` commits)
   - Other notable changes

Ask the user to confirm the version before creating the tag.
