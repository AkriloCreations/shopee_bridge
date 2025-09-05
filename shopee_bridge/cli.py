"""CLI commands for Shopee Bridge."""

import click
import json
import frappe

@click.group()
def cli():
	"""Shopee Bridge CLI."""
	pass

@cli.command()
@click.option('--hours', default=24, help='Hours to look back.')
def sync_recent(hours):
	"""Sync recent orders."""
	from shopee_bridge.api import sync_recent_orders
	result = sync_recent_orders(hours)
	click.echo(json.dumps(result, indent=2))

@cli.command()
@click.option('--year', required=True, type=int, help='Year.')
@click.option('--month', required=True, type=int, help='Month.')
def audit_month(year, month):
	"""Audit orders for month."""
	from shopee_bridge.api import audit_shopee_orders_for_month
	result = audit_shopee_orders_for_month(year, month)
	click.echo(json.dumps(result, indent=2))

@cli.command()
@click.option('--path', required=True, help='Path to sign.')
def debug_sign(path):
	"""Debug sign a path."""
	from shopee_bridge.api import debug_sign
	result = debug_sign(path)
	click.echo(json.dumps(result, indent=2))

if __name__ == '__main__':
	cli()
