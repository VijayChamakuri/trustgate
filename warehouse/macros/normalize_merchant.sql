{#
  Card-network merchant strings carry store numbers, order references and punctuation
  ("AMZN MKTP UK MN1JP3X84", "ASDA STORES 4840"). The key drops any token containing a digit,
  turns punctuation into spaces and keeps the first three remaining tokens.
#}
{% macro normalize_merchant(col) -%}
    coalesce(nullif(array_to_string(list_slice(
        list_filter(
            string_split(trim(regexp_replace(regexp_replace(upper({{ col }}), '[^A-Z0-9 ]', ' ', 'g'), '\s+', ' ', 'g')), ' '),
            lambda tok: tok <> '' and not regexp_matches(tok, '[0-9]')
        ), 1, 3), ' '), ''), 'UNKNOWN')
{%- endmacro %}
