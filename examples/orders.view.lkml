view: orders {
  sql_table_name: public.orders ;;
  description: "All customer orders with associated revenue and status data"

  dimension: id {
    primary_key: yes
    type: number
    sql: ${TABLE}.id ;;
    description: "Unique order identifier"
  }

  dimension: user_id {
    type: number
    sql: ${TABLE}.user_id ;;
    description: "Foreign key to the users table"
  }

  dimension: status {
    type: string
    sql: ${TABLE}.status ;;
    description: "Current order status (pending, complete, cancelled, returned)"
  }

  dimension_group: created {
    type: time
    timeframes: [raw, date, week, month, quarter, year]
    sql: ${TABLE}.created_at ;;
    description: "Timestamp when the order was placed"
  }

  dimension: sale_price {
    type: number
    sql: ${TABLE}.sale_price ;;
    description: "Final sale price of the order after discounts"
  }

  measure: total_revenue {
    type: sum
    sql: ${sale_price} ;;
    value_format_name: usd
    description: "Sum of all order sale prices"
    tags: ["kpi", "finance"]

    link: {
      label: "Revenue Dashboard"
      url: "/dashboards/revenue_overview"
    }

    link: {
      label: "Finance Deep Dive"
      url: "/dashboards/finance_detail"
    }
  }

  measure: order_count {
    type: count
    description: "Total number of orders"
    tags: ["kpi"]

    link: {
      label: "Orders Dashboard"
      url: "/dashboards/orders_overview"
    }
  }

  measure: average_order_value {
    type: average
    sql: ${sale_price} ;;
    value_format_name: usd
    description: "Average revenue per order"
    tags: ["key_metric"]
  }

  measure: total_orders_completed {
    type: count
    description: "Number of completed orders"
  }

  measure: conversion_rate {
    type: number
    sql: 1.0 * ${total_orders_completed} / NULLIF(${order_count}, 0) ;;
    value_format_name: percent_2
    description: "Ratio of completed orders to total orders"
    tags: ["kpi"]
  }
}
