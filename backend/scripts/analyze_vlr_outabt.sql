WITH eligible_purchases AS (
    SELECT
        p.erp_entry_id,
        p.unit_code,
        date_trunc('month', p.purchase_date)::date AS reference_month,
        p.total_liters * 0.04 AS expected_value
    FROM purchases p
    WHERE p.mapped_company_code = 'TEXACO'
      AND (
          (p.unit_code = '005' AND p.purchase_date >= DATE '2025-07-31') OR
          (p.unit_code = '007' AND p.purchase_date >= DATE '2025-09-10') OR
          (p.unit_code = '014' AND p.purchase_date >= DATE '2026-04-22') OR
          (p.unit_code = '050' AND p.purchase_date >= DATE '2025-11-21')
      )
),
expected AS (
    SELECT unit_code, reference_month, SUM(expected_value) AS expected_value
    FROM eligible_purchases
    GROUP BY unit_code, reference_month
),
outabt AS (
    SELECT p.unit_code, p.reference_month, SUM(d.other_discount) AS outabt_value
    FROM eligible_purchases p
    JOIN payable_documents d ON d.erp_entry_id = p.erp_entry_id
    GROUP BY p.unit_code, p.reference_month
),
financial_discount AS (
    SELECT p.unit_code, p.reference_month, SUM(ABS(f.value)) AS financial_discount_value
    FROM eligible_purchases p
    JOIN payable_documents d ON d.erp_entry_id = p.erp_entry_id
    JOIN financial_entries f ON f.unit_code = d.unit_code
        AND f.document_id = d.document_id
        AND f.history_code = 6204
    GROUP BY p.unit_code, p.reference_month
)
SELECT
    e.unit_code,
    COUNT(*) AS months,
    ROUND(SUM(e.expected_value), 2) AS expected_total,
    ROUND(SUM(COALESCE(o.outabt_value, 0)), 2) AS outabt_total,
    ROUND(SUM(COALESCE(f.financial_discount_value, 0)), 2) AS financial_discount_total,
    ROUND(SUM(ABS(e.expected_value - COALESCE(o.outabt_value, 0))), 2) AS outabt_absolute_error,
    ROUND(SUM(ABS(e.expected_value - COALESCE(f.financial_discount_value, 0))), 2) AS financial_absolute_error
FROM expected e
LEFT JOIN outabt o ON o.unit_code = e.unit_code AND o.reference_month = e.reference_month
LEFT JOIN financial_discount f ON f.unit_code = e.unit_code AND f.reference_month = e.reference_month
GROUP BY e.unit_code
ORDER BY e.unit_code;
