view: users {
  sql_table_name: public.users ;;
  description: "Registered customer profiles and demographics"

  dimension: id {
    primary_key: yes
    type: number
    sql: ${TABLE}.id ;;
    description: "Unique user identifier"
  }

  dimension: name {
    type: string
    sql: ${TABLE}.name ;;
    description: "Full name of the user"
  }

  dimension: email {
    type: string
    sql: ${TABLE}.email ;;
    description: "User email address"
  }

  dimension: age {
    type: number
    sql: ${TABLE}.age ;;
    description: "Age of the customer in years"
  }

  dimension: country {
    type: string
    sql: ${TABLE}.country ;;
    description: "Country of residence"
  }

  dimension_group: created {
    type: time
    timeframes: [raw, date, week, month, year]
    sql: ${TABLE}.created_at ;;
    description: "When the user account was created"
  }

  measure: user_count {
    type: count_distinct
    sql: ${id} ;;
    description: "Total number of unique registered users"
    tags: ["key_metric"]
  }
}
