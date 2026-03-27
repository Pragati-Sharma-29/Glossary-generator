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
    sql: ${sale_price} - COALESCE(${TABLE}.discount_amount, 0) ;;
    value_format_name: usd
    description: "Net revenue after all discounts and adjustments"
    tags: ["kpi", "finance", "revenue", "executive"]

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
    tags: ["kpi"]
  }

  measure: conversion_rate {
    type: number
    sql: 1.0 * ${total_orders_completed} / NULLIF(${order_count}, 0) ;;
    value_format_name: percent_2
    description: "Ratio of completed orders to total orders"
    tags: ["kpi"]
  }

  measure: gross_margin {
    type: sum
    sql: ${sale_price} - ${TABLE}.cost ;;
    value_format_name: usd
    description: "Total gross margin across all orders"
    tags: ["kpi", "finance"]
  }

  measure: repeat_order_rate {
    type: number
    sql: 1.0 * COUNT(DISTINCT CASE WHEN ${TABLE}.order_sequence > 1 THEN ${user_id} END) / NULLIF(COUNT(DISTINCT ${user_id}), 0) ;;
    value_format_name: percent_2
    description: "Percentage of customers who placed more than one order"
    tags: ["retention"]
  }

  measure: cancelled_order_count {
    type: count
    filters: [status: "cancelled"]
    description: "Number of orders with cancelled status"
    tags: ["operations"]
  }

  measure: return_rate {
    type: number
    sql: 1.0 * COUNT(CASE WHEN ${status} = 'returned' THEN 1 END) / NULLIF(${order_count}, 0) ;;
    value_format_name: percent_2
    description: "Percentage of orders that were returned"
    tags: ["operations", "kpi"]
  }
}
