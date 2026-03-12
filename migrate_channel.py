#!/usr/bin/env python3
"""
Saleor channel migration script.

Reassigns products, variants, and shipping methods from one channel to another,
preserving all existing settings, then removes the old channel assignment.

Usage:
    python migrate_channel.py           # live run
    python migrate_channel.py --dry-run # preview only, no mutations executed
"""

import argparse
import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv()

SALEOR_API_URL = os.getenv("SALEOR_API_URL")
SALEOR_AUTH_TOKEN = os.getenv("SALEOR_AUTH_TOKEN")
OLD_CHANNEL_SLUG = os.getenv("OLD_CHANNEL_SLUG")
NEW_CHANNEL_SLUG = os.getenv("NEW_CHANNEL_SLUG")


def validate_config():
    missing = [
        name
        for name, val in {
            "SALEOR_API_URL": SALEOR_API_URL,
            "SALEOR_AUTH_TOKEN": SALEOR_AUTH_TOKEN,
            "OLD_CHANNEL_SLUG": OLD_CHANNEL_SLUG,
            "NEW_CHANNEL_SLUG": NEW_CHANNEL_SLUG,
        }.items()
        if not val
    ]
    if missing:
        print(f"ERROR: Missing required env vars: {', '.join(missing)}")
        sys.exit(1)


def gql(query: str, variables: dict = None) -> dict:
    headers = {
        "Authorization": f"Bearer {SALEOR_AUTH_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {"query": query}
    if variables:
        payload["variables"] = variables

    response = requests.post(SALEOR_API_URL, json=payload, headers=headers)
    if not response.ok:
        raise RuntimeError(f"HTTP {response.status_code}: {response.text}")
    data = response.json()

    if "errors" in data:
        raise RuntimeError(f"GraphQL errors: {data['errors']}")

    return data["data"]


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

QUERY_PRODUCTS = """
query ProductsInChannel($channel: String!) {
  products(first: 50, channel: $channel) {
    totalCount
    edges {
      node {
        id
        name
        channelListings {
          id
          channel {
            id
            slug
          }
          isPublished
          publicationDate
          isAvailableForPurchase
          availableForPurchaseAt
          visibleInListings
        }
        variants {
          id
          channelListings {
            id
            channel {
              id
              slug
            }
            price {
              amount
            }
          }
        }
      }
    }
  }
}
"""

QUERY_SHIPPING_ZONES = """
query ShippingZonesInChannel($channel: String!) {
  shippingZones(first: 10, channel: $channel) {
    totalCount
    edges {
      node {
        id
        shippingMethods {
          id
          name
          channelListings {
            id
            channel {
              id
              slug
            }
            price {
              amount
            }
          }
        }
      }
    }
  }
}
"""

QUERY_CHANNEL_BY_SLUG = """
query ChannelBySlug($slug: String!) {
  channel(slug: $slug) {
    id
    slug
  }
}
"""

# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------

MUTATION_PRODUCT_CHANNEL_LISTING_UPDATE = """
mutation ProductChannelListingUpdate($id: ID!, $input: ProductChannelListingUpdateInput!) {
  productChannelListingUpdate(id: $id, input: $input) {
    errors {
      field
      message
      code
    }
  }
}
"""

MUTATION_PRODUCT_VARIANT_BULK_UPDATE = """
mutation ProductVariantBulkUpdate($product: ID!, $variants: [ProductVariantBulkUpdateInput!]!) {
  productVariantBulkUpdate(product: $product, variants: $variants) {
    errors {
      field
      message
      code
    }
  }
}
"""

MUTATION_SHIPPING_METHOD_CHANNEL_LISTING_UPDATE = """
mutation ShippingMethodChannelListingUpdate($id: ID!, $input: ShippingMethodChannelListingInput!) {
  shippingMethodChannelListingUpdate(id: $id, input: $input) {
    errors {
      field
      message
      code
    }
  }
}
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def check_mutation_errors(result: dict, mutation_name: str, label: str) -> bool:
    """Returns True if there were errors, False if clean."""
    errors = result.get(mutation_name, {}).get("errors", [])
    if errors:
        for err in errors:
            print(f"  ERROR [{label}]: {err.get('field')} — {err.get('message')} ({err.get('code')})")
        return True
    return False


def get_channel_id(slug: str) -> str:
    data = gql(QUERY_CHANNEL_BY_SLUG, {"slug": slug})
    channel = data.get("channel")
    if not channel:
        print(f"ERROR: Channel with slug '{slug}' not found.")
        sys.exit(1)
    return channel["id"]


# ---------------------------------------------------------------------------
# Migration steps
# ---------------------------------------------------------------------------

def migrate_products(old_channel_id: str, old_channel_slug: str, new_channel_id: str, new_channel_slug: str, dry_run: bool):
    print("\n=== Migrating products and variants ===")

    data = gql(QUERY_PRODUCTS, {"channel": OLD_CHANNEL_SLUG})
    products_data = data["products"]
    total = products_data["totalCount"]
    print(f"Found {total} products in channel '{OLD_CHANNEL_SLUG}' (fetching up to 50)")

    for edge in products_data["edges"]:
        product = edge["node"]
        product_id = product["id"]
        product_name = product["name"]

        # Find old channel listing for this product
        old_listing = next(
            (cl for cl in product["channelListings"] if cl["channel"]["slug"] == OLD_CHANNEL_SLUG),
            None,
        )
        if not old_listing:
            print(f"  SKIP product '{product_name}': no listing for old channel")
            continue

        print(f"\nProduct: {product_name} ({product_id})")

        # --- Product channel listing update ---
        product_update_input = {
            "updateChannels": [
                {
                    "channelId": new_channel_id,
                    "isPublished": old_listing["isPublished"],
                    "publicationDate": old_listing.get("publicationDate"),
                    "isAvailableForPurchase": old_listing["isAvailableForPurchase"],
                    "availableForPurchaseDate": (old_listing.get("availableForPurchaseAt") or "")[:10] or None,
                    "visibleInListings": old_listing["visibleInListings"],
                }
            ],
            "removeChannels": [old_channel_id],
        }

        if dry_run:
            print(f"  [DRY RUN] Would update product channel listing:")
            print(f"    add new channel: id={new_channel_id} slug={new_channel_slug} | "
                  f"isPublished={old_listing['isPublished']}, "
                  f"isAvailableForPurchase={old_listing['isAvailableForPurchase']}, "
                  f"visibleInListings={old_listing['visibleInListings']}")
            print(f"    remove old channel: id={old_channel_id} slug={old_channel_slug}")
        else:
            try:
                result = gql(
                    MUTATION_PRODUCT_CHANNEL_LISTING_UPDATE,
                    {"id": product_id, "input": product_update_input},
                )
                if not check_mutation_errors(result, "productChannelListingUpdate", product_name):
                    print(f"  OK product listing updated")
            except Exception as e:
                print(f"  ERROR updating product listing for '{product_name}': {e}")

        # --- Variant bulk update ---
        variants = product.get("variants", [])
        if not variants:
            print(f"  No variants found for '{product_name}'")
            continue

        bulk_inputs = []
        for variant in variants:
            variant_id = variant["id"]
            old_variant_listing = next(
                (cl for cl in variant["channelListings"] if cl["channel"]["slug"] == OLD_CHANNEL_SLUG),
                None,
            )
            if not old_variant_listing:
                print(f"  SKIP variant {variant_id}: no listing for old channel")
                continue

            price = old_variant_listing["price"]["amount"]

            if dry_run:
                print(f"  [DRY RUN] Would update variant {variant_id}: price={price} | "
                      f"add new channel: id={new_channel_id} slug={new_channel_slug} | "
                      f"remove old channel: id={old_channel_id} slug={old_channel_slug}")
            else:
                bulk_inputs.append({
                    "id": variant_id,
                    "channelListings": {
                        "create": [{"channelId": new_channel_id, "price": price}],
                        "remove": [old_variant_listing["id"]],
                    },
                })

        if not dry_run and bulk_inputs:
            try:
                result = gql(
                    MUTATION_PRODUCT_VARIANT_BULK_UPDATE,
                    {"product": product_id, "variants": bulk_inputs},
                )
                if not check_mutation_errors(result, "productVariantBulkUpdate", product_name):
                    print(f"  OK {len(bulk_inputs)} variant(s) updated")
            except Exception as e:
                print(f"  ERROR updating variants for '{product_name}': {e}")


def migrate_shipping(old_channel_id: str, old_channel_slug: str, new_channel_id: str, new_channel_slug: str, dry_run: bool):
    print("\n=== Migrating shipping methods ===")

    data = gql(QUERY_SHIPPING_ZONES, {"channel": OLD_CHANNEL_SLUG})
    zones_data = data["shippingZones"]
    total = zones_data["totalCount"]
    print(f"Found {total} shipping zone(s) in channel '{OLD_CHANNEL_SLUG}' (fetching up to 10)")

    for zone_edge in zones_data["edges"]:
        zone = zone_edge["node"]
        for method in zone.get("shippingMethods", []):
            method_id = method["id"]
            method_name = method["name"]

            old_listing = next(
                (cl for cl in method["channelListings"] if cl["channel"]["slug"] == OLD_CHANNEL_SLUG),
                None,
            )
            if not old_listing:
                print(f"  SKIP shipping method '{method_name}': no listing for old channel")
                continue

            price = old_listing["price"]["amount"]
            print(f"\nShipping method: {method_name} ({method_id}), price={price}")

            if dry_run:
                print(f"  [DRY RUN] Would update shipping method: price={price} | "
                      f"add new channel: id={new_channel_id} slug={new_channel_slug} | "
                      f"remove old channel: id={old_channel_id} slug={old_channel_slug}")
            else:
                update_input = {
                    "addChannels": [
                        {
                            "channelId": new_channel_id,
                            "price": price,
                        }
                    ],
                    "removeChannels": [old_channel_id],
                }
                try:
                    result = gql(
                        MUTATION_SHIPPING_METHOD_CHANNEL_LISTING_UPDATE,
                        {"id": method_id, "input": update_input},
                    )
                    if not check_mutation_errors(result, "shippingMethodChannelListingUpdate", method_name):
                        print(f"  OK shipping method updated")
                except Exception as e:
                    print(f"  ERROR updating shipping method '{method_name}': {e}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Migrate Saleor channel listings to a new channel.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview planned operations without executing any mutations.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--products",
        action="store_true",
        help="Migrate only products and variants.",
    )
    group.add_argument(
        "--shipping",
        action="store_true",
        help="Migrate only shipping methods.",
    )
    args = parser.parse_args()
    dry_run = args.dry_run

    # If neither flag given, run both
    run_products = args.products or (not args.products and not args.shipping)
    run_shipping = args.shipping or (not args.products and not args.shipping)

    validate_config()

    if dry_run:
        print("*** DRY RUN MODE — no mutations will be executed ***")

    print(f"Migrating from channel '{OLD_CHANNEL_SLUG}' → '{NEW_CHANNEL_SLUG}'")
    print(f"API: {SALEOR_API_URL}")

    print("\nFetching channel IDs...")
    old_channel_id = get_channel_id(OLD_CHANNEL_SLUG)
    new_channel_id = get_channel_id(NEW_CHANNEL_SLUG)
    print(f"  Old channel ID: {old_channel_id}")
    print(f"  New channel ID: {new_channel_id}")

    if run_products:
        migrate_products(old_channel_id, OLD_CHANNEL_SLUG, new_channel_id, NEW_CHANNEL_SLUG, dry_run)
    if run_shipping:
        migrate_shipping(old_channel_id, OLD_CHANNEL_SLUG, new_channel_id, NEW_CHANNEL_SLUG, dry_run)

    print("\n=== Migration complete ===")


if __name__ == "__main__":
    main()
