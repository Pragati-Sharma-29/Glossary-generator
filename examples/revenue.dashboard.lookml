- dashboard: revenue_overview
  title: Revenue Overview Dashboard
  layout: newspaper
  elements:
  - title: Total Revenue
    name: total_revenue_tile
    model: ecommerce
    explore: orders
    type: single_value
    fields: [orders.total_revenue, orders.order_count]
    limit: 500
    row: 0
    col: 0
    width: 8
    height: 4

  - title: Revenue by Status
    name: revenue_by_status
    model: ecommerce
    explore: orders
    type: looker_column
    fields: [orders.status, orders.total_revenue]
    sorts: [orders.total_revenue desc]
    limit: 500
    row: 4
    col: 0
    width: 12
    height: 6
