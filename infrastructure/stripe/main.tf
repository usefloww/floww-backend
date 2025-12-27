resource "stripe_product" "hobby_tier" {
  name        = "Floww Hobby"
  description = "For solo builders."
}

resource "stripe_price" "hobby_price" {
  product        = stripe_product.hobby_tier.id
  currency       = "eur"
  unit_amount    = 1000 # 10.00 EUR
  billing_scheme = "per_unit"
  tax_behaviour  = "unspecified"
  recurring {
    interval = "month"
  }
}

resource "stripe_product" "team_tier" {
  name        = "Floww Team"
  description = "For small to medium teams."
}

resource "stripe_price" "team_price" {
  product        = stripe_product.team_tier.id
  currency       = "eur"
  unit_amount    = 5000 # 50.00 EUR
  billing_scheme = "per_unit"
  tax_behaviour  = "unspecified"
  recurring {
    interval = "month"
  }
}

# --- Outputs ---
output "hobby_product_id" {
  value = stripe_product.hobby_tier.id
}

output "team_product_id" {
  value = stripe_product.team_tier.id
}

output "hobby_price_id" {
  value = stripe_price.hobby_price.id
}

output "team_price_id" {
  value = stripe_price.team_price.id
}
