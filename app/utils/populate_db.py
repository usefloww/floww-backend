#!/usr/bin/env python3
"""
Database Population Script

This script connects to the production server, extracts database credentials,
creates a secure tunnel, and copies the production data to the local database.

Usage:
    python populate_db.py [--ssh-key-path PATH] [--local-db-url URL] [--tables TABLE1,TABLE2,...]
"""

import argparse
import asyncio
import logging
import os
import re
import signal
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

import asyncpg

# Import local settings
try:
    from app.settings import settings

    DEFAULT_LOCAL_DB_URL = settings.DATABASE_URL
except ImportError:
    DEFAULT_LOCAL_DB_URL = "postgresql://admin:secret@localhost:5432/postgres"

# Setup logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class DatabasePopulator:
    def __init__(
        self,
        ssh_host: str = "flow-server",
        ssh_user: str = "ec2-user",
        ssh_key_path: Optional[str] = None,
        local_db_url: str = DEFAULT_LOCAL_DB_URL,
        remote_env_path: str = "/home/ec2-user/infrastructure/services/floww-backend/.env",
    ):
        self.ssh_host = ssh_host
        self.ssh_user = ssh_user
        self.ssh_key_path = ssh_key_path or os.path.expanduser("~/.ssh/id_rsa")
        self.local_db_url = local_db_url
        self.remote_env_path = remote_env_path
        self.tunnel_process: Optional[subprocess.Popen] = None
        self.local_tunnel_port = 5433  # Different from local PostgreSQL port

    def _run_ssh_command(
        self, command: str, capture_output: bool = True
    ) -> subprocess.CompletedProcess:
        """Run a command via SSH"""
        ssh_cmd = [
            "ssh",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            "LogLevel=ERROR",
        ]

        if self.ssh_key_path and os.path.exists(self.ssh_key_path):
            ssh_cmd.extend(["-i", self.ssh_key_path])

        ssh_cmd.extend([f"{self.ssh_user}@{self.ssh_host}", command])

        logger.info(f"Running SSH command: {' '.join(ssh_cmd[:-1])} [COMMAND_HIDDEN]")

        return subprocess.run(
            ssh_cmd, capture_output=capture_output, text=True, timeout=30
        )

    def get_remote_env_vars(self) -> Dict[str, str]:
        """Fetch environment variables from remote .env file"""
        logger.info(f"Fetching environment variables from {self.remote_env_path}")

        # Check if file exists
        check_cmd = (
            f"test -f {self.remote_env_path} && echo 'exists' || echo 'not found'"
        )
        result = self._run_ssh_command(check_cmd)

        if result.returncode != 0 or "not found" in result.stdout:
            raise FileNotFoundError(
                f"Remote .env file not found at {self.remote_env_path}"
            )

        # Read the .env file
        cat_cmd = f"cat {self.remote_env_path}"
        result = self._run_ssh_command(cat_cmd)

        if result.returncode != 0:
            raise RuntimeError(f"Failed to read remote .env file: {result.stderr}")

        # Parse environment variables
        env_vars = {}
        for line in result.stdout.split("\n"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                # Remove quotes if present
                value = value.strip("\"'")
                env_vars[key] = value

        logger.info(f"Found {len(env_vars)} environment variables")
        return env_vars

    def parse_db_url(self, db_url: str) -> Dict[str, str]:
        """Parse database URL into components"""
        # Format: postgresql://user:password@host:port/database
        pattern = r"postgresql://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)"
        match = re.match(pattern, db_url)

        if not match:
            raise ValueError(f"Invalid database URL format: {db_url}")

        return {
            "user": match.group(1),
            "password": match.group(2),
            "host": match.group(3),
            "port": match.group(4),
            "database": match.group(5),
        }

    def create_ssh_tunnel(self, remote_db_config: Dict[str, str]) -> None:
        """Create SSH tunnel to remote database"""
        logger.info(
            f"Creating SSH tunnel to {remote_db_config['host']}:{remote_db_config['port']}"
        )

        tunnel_cmd = [
            "ssh",
            "-N",  # Don't execute remote commands
            "-L",
            f"{self.local_tunnel_port}:{remote_db_config['host']}:{remote_db_config['port']}",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            "LogLevel=ERROR",
        ]

        if self.ssh_key_path and os.path.exists(self.ssh_key_path):
            tunnel_cmd.extend(["-i", self.ssh_key_path])

        tunnel_cmd.append(f"{self.ssh_user}@{self.ssh_host}")

        logger.info(f"Starting SSH tunnel: {' '.join(tunnel_cmd)}")

        self.tunnel_process = subprocess.Popen(
            tunnel_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )

        # Wait a moment for tunnel to establish
        time.sleep(3)

        if self.tunnel_process.poll() is not None:
            _, stderr = self.tunnel_process.communicate()
            raise RuntimeError(f"SSH tunnel failed to start: {stderr.decode()}")

        logger.info(f"SSH tunnel established on localhost:{self.local_tunnel_port}")

    def close_ssh_tunnel(self) -> None:
        """Close SSH tunnel"""
        if self.tunnel_process and self.tunnel_process.poll() is None:
            logger.info("Closing SSH tunnel")
            self.tunnel_process.terminate()
            try:
                self.tunnel_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.tunnel_process.kill()
                self.tunnel_process.wait()

    async def get_table_list(self, conn: asyncpg.Connection) -> List[str]:
        """Get list of all user tables from database"""
        query = """
            SELECT tablename
            FROM pg_tables
            WHERE schemaname = 'public'
            ORDER BY tablename
        """
        result = await conn.fetch(query)
        return [row["tablename"] for row in result]

    async def get_table_dependencies(
        self, conn: asyncpg.Connection
    ) -> Dict[str, List[str]]:
        """Get foreign key dependencies for all tables"""
        query = """
            SELECT
                tc.table_name as child_table,
                ccu.table_name as parent_table
            FROM
                information_schema.table_constraints AS tc
                JOIN information_schema.key_column_usage AS kcu
                  ON tc.constraint_name = kcu.constraint_name
                  AND tc.table_schema = kcu.table_schema
                JOIN information_schema.constraint_column_usage AS ccu
                  ON ccu.constraint_name = tc.constraint_name
                  AND ccu.table_schema = tc.table_schema
            WHERE
                tc.constraint_type = 'FOREIGN KEY'
                AND tc.table_schema = 'public'
        """
        result = await conn.fetch(query)

        # Build dependency mapping: table -> list of tables it depends on
        dependencies = {}
        for row in result:
            child_table = row["child_table"]
            parent_table = row["parent_table"]

            if child_table not in dependencies:
                dependencies[child_table] = []

            if parent_table != child_table:  # Avoid self-references
                dependencies[child_table].append(parent_table)

        return dependencies

    def topological_sort(
        self, tables: List[str], dependencies: Dict[str, List[str]]
    ) -> List[str]:
        """Sort tables in dependency order using topological sort"""
        # Create a copy of dependencies to avoid modifying the original
        deps = {table: list(dependencies.get(table, [])) for table in tables}
        result = []

        # Find tables with no dependencies
        no_deps = [table for table in tables if not deps[table]]

        while no_deps:
            # Remove a table with no dependencies
            current = no_deps.pop(0)
            result.append(current)

            # Remove this table from all dependency lists
            for table in tables:
                if current in deps[table]:
                    deps[table].remove(current)
                    # If this table now has no dependencies, add it to no_deps
                    if not deps[table] and table not in result and table not in no_deps:
                        no_deps.append(table)

        # Check for circular dependencies
        remaining = [table for table in tables if table not in result]
        if remaining:
            logger.warning(f"Circular dependencies detected for tables: {remaining}")
            logger.warning(
                "Adding them in original order - foreign key errors may occur"
            )
            result.extend(remaining)

        return result

    async def copy_table_data(
        self,
        remote_conn: asyncpg.Connection,
        local_conn: asyncpg.Connection,
        table_name: str,
    ) -> int:
        """Copy data from remote table to local table"""
        logger.info(f"Copying table: {table_name}")

        # First, truncate the local table
        await local_conn.execute(f'TRUNCATE TABLE "{table_name}" CASCADE')

        # Get all data from remote table
        remote_data = await remote_conn.fetch(f'SELECT * FROM "{table_name}"')

        if not remote_data:
            logger.info(f"  No data in {table_name}")
            return 0

        # Get column names
        columns = list(remote_data[0].keys())
        column_list = ", ".join(f'"{col}"' for col in columns)
        placeholders = ", ".join(f"${i + 1}" for i in range(len(columns)))

        # Insert data in batches
        insert_query = (
            f'INSERT INTO "{table_name}" ({column_list}) VALUES ({placeholders})'
        )

        # Convert records to tuples for batch insert
        data_tuples = [tuple(record.values()) for record in remote_data]

        # Insert in batches of 1000
        batch_size = 1000
        total_inserted = 0

        for i in range(0, len(data_tuples), batch_size):
            batch = data_tuples[i : i + batch_size]
            await local_conn.executemany(insert_query, batch)
            total_inserted += len(batch)
            logger.info(f"  Inserted {total_inserted}/{len(data_tuples)} rows")

        logger.info(f"  ✓ Completed {table_name}: {total_inserted} rows")
        return total_inserted

    async def copy_database_data(
        self,
        remote_db_config: Dict[str, str],
        tables_to_copy: Optional[List[str]] = None,
        disable_fk_checks: bool = False,
    ) -> None:
        """Copy data from remote database to local database"""
        logger.info("Starting database data copy")

        # Build connection URLs
        remote_url = f"postgresql://{remote_db_config['user']}:{remote_db_config['password']}@localhost:{self.local_tunnel_port}/{remote_db_config['database']}"

        # Connect to both databases
        remote_conn = await asyncpg.connect(remote_url)
        local_conn = await asyncpg.connect(self.local_db_url)

        try:
            # Get list of tables to copy
            if tables_to_copy:
                tables = tables_to_copy
                logger.info(f"Copying specified tables: {', '.join(tables)}")
            else:
                tables = await self.get_table_list(remote_conn)
                logger.info(f"Found {len(tables)} tables to copy")

            # Get table dependencies and sort in correct order
            if not disable_fk_checks:
                logger.info("Analyzing table dependencies...")
                dependencies = await self.get_table_dependencies(remote_conn)
                tables = self.topological_sort(tables, dependencies)
                logger.info(
                    f"Tables will be copied in dependency order: {', '.join(tables)}"
                )

            # Start a transaction for the entire copy operation
            async with local_conn.transaction():
                logger.info("Starting transaction for data copy")

                # Optionally disable foreign key checks
                if disable_fk_checks:
                    logger.info("Disabling foreign key constraints")
                    await local_conn.execute("SET session_replication_role = replica")

                # Copy each table
                total_rows = 0
                for table in tables:
                    try:
                        rows_copied = await self.copy_table_data(
                            remote_conn, local_conn, table
                        )
                        total_rows += rows_copied
                    except Exception as e:
                        logger.error(f"Failed to copy table {table}: {e}")
                        raise  # Re-raise to trigger transaction rollback

                # Re-enable foreign key checks if they were disabled
                if disable_fk_checks:
                    logger.info("Re-enabling foreign key constraints")
                    await local_conn.execute("SET session_replication_role = DEFAULT")

                logger.info(f"✓ Transaction committed! Total rows copied: {total_rows}")

        finally:
            await remote_conn.close()
            await local_conn.close()

    async def populate_database(
        self,
        tables_to_copy: Optional[List[str]] = None,
        disable_fk_checks: bool = False,
    ) -> None:
        """Main method to populate local database with remote data"""
        try:
            # Step 1: Get remote environment variables
            env_vars = self.get_remote_env_vars()

            if "DATABASE_URL" not in env_vars:
                raise ValueError("DATABASE_URL not found in remote .env file")

            database_url = env_vars["DATABASE_URL"]
            database_url = database_url.replace("+asyncpg", "")

            remote_db_config = self.parse_db_url(database_url)
            logger.info(
                f"Remote database: {remote_db_config['host']}:{remote_db_config['port']}"
            )

            # Step 2: Create SSH tunnel
            self.create_ssh_tunnel(remote_db_config)

            # Step 3: Copy database data
            await self.copy_database_data(
                remote_db_config, tables_to_copy, disable_fk_checks
            )

            logger.info("Database population completed successfully!")

        finally:
            # Always close SSH tunnel
            self.close_ssh_tunnel()


def signal_handler(signum: int, frame: Any) -> None:
    """Handle interrupt signals gracefully"""
    logger.info("Received interrupt signal, cleaning up...")
    sys.exit(1)


async def main():
    # Set up signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    parser = argparse.ArgumentParser(
        description="Populate local database with production data"
    )
    parser.add_argument(
        "--ssh-key-path", help="Path to SSH private key file (default: ~/.ssh/id_rsa)"
    )
    parser.add_argument(
        "--local-db-url",
        default=DEFAULT_LOCAL_DB_URL,
        help=f"Local database URL (default: {DEFAULT_LOCAL_DB_URL})",
    )
    parser.add_argument(
        "--ssh-host",
        default="flow-server",
        help="SSH host to connect to (default: flow-server)",
    )
    parser.add_argument(
        "--ssh-user", default="ec2-user", help="SSH user (default: ec2-user)"
    )
    parser.add_argument(
        "--remote-env-path",
        default="/home/ec2-user/infrastructure/services/floww-backend/.env",
        help="Path to remote .env file",
    )
    parser.add_argument(
        "--tables",
        help="Comma-separated list of specific tables to copy (default: copy all tables)",
    )
    parser.add_argument(
        "--disable-fk-checks",
        action="store_true",
        help="Disable foreign key constraints during copy (faster but less safe)",
    )

    args = parser.parse_args()

    # Parse tables argument
    tables_to_copy = None
    if args.tables:
        tables_to_copy = [table.strip() for table in args.tables.split(",")]

    populator = DatabasePopulator(
        ssh_host=args.ssh_host,
        ssh_user=args.ssh_user,
        ssh_key_path=args.ssh_key_path,
        local_db_url=args.local_db_url,
        remote_env_path=args.remote_env_path,
    )

    try:
        await populator.populate_database(tables_to_copy, args.disable_fk_checks)
    except Exception as e:
        logger.error(f"Database population failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
