#!/usr/bin/env python3
"""Authoritative file closure required to import the production server.

Keep this deliberately small: frontend/dist is a build artifact copied from the
web build stage, while these build-context files are the Python/data runtime.
"""
from __future__ import annotations


RUNTIME_FILES = (
    "server.py",
    "agent_mcp.py",
    "community.py",
    "station_truth.py",
    "scripts/source_policy.py",
    "scripts/json_recordset.py",
    "scripts/global_source_catalog.py",
    "scripts/intelligence_index.py",
    "scripts/source_transport.py",
    "data/free_intelligence.json",
    "data/seed_leads.json",
    "data/source_policy_exclusions.json",
)
