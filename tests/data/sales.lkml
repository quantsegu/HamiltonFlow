view: orders {
  sql_table_name: main.orders ;;
  dimension: order_id { primary_key: yes type: number sql: ${TABLE}.order_id ;; }
  dimension: customer_id { type: number sql: ${TABLE}.customer_id ;; }
  dimension: status { type: string sql: ${TABLE}.status ;; }
  dimension: amount_cents { type: number sql: ${TABLE}.amount * 100 ;; hidden: yes }
  dimension_group: ordered { type: time timeframes: [raw, date, month] sql: ${TABLE}.ordered_at ;; }
  dimension: dynamic { type: string sql: {% if _user_attributes['x'] == '1' %}a{% else %}b{% endif %} ;; }
  measure: revenue { type: sum sql: ${TABLE}.amount ;; }
  measure: order_count { type: count }
  measure: buyers { type: count_distinct sql: ${customer_id} ;; }
  measure: paid_revenue { type: sum sql: ${TABLE}.amount ;; filters: [status: "paid"] }
  measure: aov { type: number sql: ${revenue} / NULLIF(${order_count}, 0) ;; }
}
view: customers {
  sql_table_name: main.customers ;;
  dimension: customer_id { primary_key: yes type: number sql: ${TABLE}.customer_id ;; }
  dimension: country { type: string sql: ${TABLE}.country ;; }
}
view: derived { derived_table: { sql: select 1 ;; } dimension: x { type: number sql: ${TABLE}.x ;; } }
explore: orders {
  join: customers { type: left_outer relationship: many_to_one sql_on: ${orders.customer_id} = ${customers.customer_id} ;; }
}
