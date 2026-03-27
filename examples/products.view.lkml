view: products {
  sql_table_name: public.products ;;
  description: "Product catalogue with pricing and inventory information"

  dimension: id {
    primary_key: yes
    type: number
    sql: ${TABLE}.id ;;
    description: "Unique product identifier"
  }

  dimension: name {
    type: string
    sql: ${TABLE}.name ;;
    description: "Product display name"
  }

  dimension: category {
    type: string
    sql: ${TABLE}.category ;;
    description: "Product category classification"
  }

  dimension: brand {
    type: string
    sql: ${TABLE}.brand ;;
    description: "Product brand name"
  }

  dimension: is_active {
    type: string
    sql: CASE WHEN ${TABLE}.discontinued_at IS NULL THEN 'Active' ELSE 'Discontinued' END ;;
    description: "Product availability status label"
  }

  dimension: retail_price {
    type: number
    sql: ${TABLE}.retail_price ;;
    value_format_name: usd
    description: "Manufacturer suggested retail price"
  }

  measure: product_count {
    type: count
    description: "Total number of active products in catalogue"
  }

  measure: average_price {
    type: average
    sql: ${retail_price} ;;
    value_format_name: usd
    description: "Average retail price across all products"
  }
}
