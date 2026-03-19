# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

GRCen (pronounced "gurken") is a free and open-source GRC (Governance, Risk, Compliance) tool. It manages assets and their relationships as a graph — any object can be linked to any other with a described relationship, searchable from any node.

**Status:** Early-stage — architecture and asset model are defined but no implementation code exists yet. The .gitignore suggests a Python-based stack.

## Asset Model

The core domain has 12 asset types: People, Policies, Products, Systems, Devices, Data Categories, Audits, Requirements, Processes, Intellectual Property, Risks, and Organizational Units. Any asset can link to any other with a relationship description. Assets can also be associated with evidence, documents, and URLs.

## Key Features (Planned)

- Asset and relation database (graph-oriented)
- Visual node graphs for selected objects
- Bulk import of assets and relationships
- Customizable exports
- Schedulable alerts (annual reviews, audits, processes)

## Design Philosophy

The system is a graph of assets and relationships. Searchability from any node and visual representation of relationships are primary concerns. The tool should not impose a paradigm — it exists to map ownership and relationships as they actually are.
