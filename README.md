# GRCen
GRCen (pronounced 'gurken') is a free and open source GRC tool for anyone who wants to manage a GRC program without shoehorning another tool into doing it that's not meant to.

## Asset Types

* People
* Policies
* Products
* Systems
* Devices
* Data Categories
* Audits
* Requirements
* Processes
* Intellectual Property
* Risks
* Organizational Units

## Design Philosophy

The purpose of a GRC system is to provide a complete map of ownership and relationships of assets. This requires the ability to link any and all objects, with links that describe the relationship between them. In order for this information to be useful, it needs to be searchable from any node in the graph, and give an immediate understanding of what and how that node relates to other assets.

A visual representation of objects' relationships is important.

Objects must be associable with evidence, documents, and locations (e.g. URLs).

## Key features

* Asset and relation database
* Visual node graphs for selected objects
* Bulk import of assets (and relationships)
* Customizable exports
* Schedulable alerts (e.g. for annual reviews, audits, other processes, etc)

## Quick Start

### Docker Compose (recommended)

```bash
docker compose up --build
```

This starts PostgreSQL 16 and the GRCen app on `http://localhost:8000`. Data persists across restarts via a Docker volume.

Create an admin user:

```bash
docker compose exec app python -m grcen.main createadmin
```

### Local Development

Requirements: Python 3.12+, a running PostgreSQL instance.

```bash
# Create and activate a virtualenv
python3 -m venv .venv
source .venv/bin/activate

# Install with dev dependencies
pip install -e ".[dev]"

# Configure database connection
cp .env.example .env
# Edit .env with your PostgreSQL credentials

# Create admin user
grcen createadmin

# Run the dev server (with auto-reload)
grcen runserver
```

The app will be available at `http://localhost:8000`. Schema tables are created automatically on startup.

### Running Tests

Tests require a PostgreSQL database (default: `grcen_test` on localhost).

```bash
# Create the test database
createdb grcen_test

# Run tests
pytest
```

Override the test database URL with `TEST_DATABASE_URL`:

```bash
TEST_DATABASE_URL=postgresql://user:pass@host:5432/mydb pytest
```

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql://grcen:grcen@localhost:5432/grcen` | PostgreSQL connection string |
| `SECRET_KEY` | `change-me-to-a-random-secret-key` | Session signing key |
| `DEBUG` | `false` | Enable debug mode and auto-reload |
| `UPLOAD_DIR` | `./uploads` | File attachment storage path |

### Linting

```bash
ruff check src/
```
