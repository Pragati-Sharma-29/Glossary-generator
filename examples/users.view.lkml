view: users {
  sql_table_name: analytics_v2.dim_users ;;
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

  dimension: email_domain {
    type: string
    sql: SPLIT_PART(${TABLE}.email, '@', 2) ;;
    description: "Domain portion of user email address"
  }

  dimension: age {
    type: tier
    tiers: [18, 25, 35, 50, 65]
    sql: ${TABLE}.age ;;
    style: integer
    description: "Customer age bracket"
  }

  dimension: country {
    type: string
    sql: ${TABLE}.country_code ;;
    description: "ISO country code of residence"
  }

  dimension: lifetime_value_tier {
    type: tier
    tiers: [0, 100, 500, 1000, 5000]
    sql: ${TABLE}.lifetime_spend ;;
    style: integer
    description: "Customer segmentation by total historical spend"
    tags: ["segmentation"]
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
