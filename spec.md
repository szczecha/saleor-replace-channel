Saleor doesn't allow changing a channel's currency.
This is an intentional platform constraint, it caused data integrity problems.

So while you cannot change currency in channel you can create new one and use script to reassign products and prices.

1. Create new channel with desired currency
2. Prepare a script to automatically reassign all product and variant channel listings to the new LKR channel - so you don't lose your catalogue configuration
3. Prepare a script to automatically reassign shipping method channel listings - any shipping methods previously assigned to the one channel will also need to be reassigned to the new channel, including updating their prices

## Decisions

- **Pagination**: Not in first iteration. Script handles up to 50 products and 10 variants per product, 10 shipping zones.
- **Date fields**: Preserve `publishedAt` and `availableForPurchaseDate` alongside boolean flags.
- **Variant updates**: Use `productVariantBulkUpdate` (bulk per product).
- **Old channel removal**: After adding to new channel, remove old channel from products, variants, and shipping methods.
- **Price currency**: Use same numeric price amount in new channel (no conversion).
- **Error handling**: Log errors and continue (do not stop on first error).
- **Dry run**: Support `--dry-run` flag — prints what would be done without executing mutations.

## Acceptance Criteria

- [ ] Script reads config from `.env` file: `SALEOR_API_URL`, `SALEOR_AUTH_TOKEN`, `OLD_CHANNEL_SLUG`, `NEW_CHANNEL_SLUG`
- [ ] `--dry-run` flag prints planned operations without executing any mutations
- [ ] All products in old channel are added to new channel with same `isPublished`, `publishedAt`, `isAvailableForPurchase`, `availableForPurchaseDate`, `visibleInListings`
- [ ] Old channel listing is removed from each product after adding to new channel
- [ ] All product variants are added to new channel with same price amount (using `productVariantBulkUpdate`)
- [ ] Old channel listing is removed from each variant after adding to new channel
- [ ] All shipping methods in zones assigned to old channel are added to new channel with same price amount
- [ ] Old channel listing is removed from each shipping method after reassignment
- [ ] Errors are logged per item and script continues processing remaining items
- [ ] Script logs progress clearly (product name, variant id, shipping method name)


## Prerequisites

`.env` file with:
- `SALEOR_API_URL` — GraphQL endpoint URL
- `SALEOR_AUTH_TOKEN` — App token with `MANAGE_PRODUCTS` and `MANAGE_SHIPPING` permissions
- `OLD_CHANNEL_SLUG` — slug of the channel to migrate from
- `NEW_CHANNEL_SLUG` — slug of the channel to migrate to

Authorization header:
```json
{
  "Authorization": "Bearer <app auth token>"
}
```

## Implementation

Write a Python script using the Saleor GraphQL API that:
1. Queries all products assigned to old channel with their channel listings and variant listings
2. For each product calls `productChannelListingUpdate` to add new channel and remove old channel, preserving all existing settings
3. For each product's variants calls `productVariantBulkUpdate` to add new channel with same price and remove old channel
4. Queries all shipping zones assigned to old channel
5. For each shipping method calls `shippingMethodChannelListingUpdate` to add new channel with same price and remove old channel

## Reference Queries

Query products:
```graphql
query ProductsInChannel {
  products(first: 50, channel: "slug") {
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
          publishedAt
          isAvailableForPurchase
          availableForPurchaseDate
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
```

Query shipping zones:
```graphql
query shippingZones {
  shippingZones(first: 10, channel: "slug") {
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
```
