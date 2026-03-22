view: dynamic_fields {
  sql_table_name: analytics.events ;;

  dimension: region_column {
    type: string
    description: "Region column that changes based on user attributes"
    sql:
      {% if _user_attributes['region'] == 'EU' %}
        ${TABLE}.region_eu
      {% elsif _user_attributes['region'] == 'APAC' %}
        ${TABLE}.region_apac
      {% else %}
        ${TABLE}.region_global
      {% endif %} ;;
  }

  dimension: date_format {
    type: string
    description: "Date formatted based on locale"
    sql:
      {% if _user_attributes['locale'] == 'US' %}
        DATE_FORMAT(${TABLE}.created_at, '%m/%d/%Y')
      {% else %}
        DATE_FORMAT(${TABLE}.created_at, '%d/%m/%Y')
      {% endif %} ;;
  }

  measure: dynamic_sum {
    type: sum
    description: "Sum that varies by database dialect"
    tags: ["metric"]
    sql:
      {% if _dialect._name == 'bigquery' %}
        ${TABLE}.amount_bq
      {% elsif _dialect._name == 'snowflake' %}
        ${TABLE}.amount_sf
      {% else %}
        ${TABLE}.amount
      {% endif %} ;;
  }

  dimension: case_example {
    type: string
    description: "Uses case/when for tier assignment"
    sql:
      {% case _user_attributes['tier'] %}
        {% when 'gold' %}
          ${TABLE}.gold_price
        {% when 'silver' %}
          ${TABLE}.silver_price
        {% else %}
          ${TABLE}.standard_price
      {% endcase %} ;;
  }
}
