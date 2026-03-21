connection: "production_db"

include: "*.view.lkml"
include: "*.dashboard.lkml"

explore: orders {
  description: "Explore for analysing customer orders, revenue and fulfilment metrics"
  join: users {
    sql_on: ${orders.user_id} = ${users.id} ;;
    relationship: many_to_one
  }
}

explore: products {
  description: "Product catalogue exploration with inventory and pricing data"
}
