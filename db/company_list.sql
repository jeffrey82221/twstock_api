CREATE or REPLACE VIEW investment.company_list 
AS 
SELECT DISTINCT stk_code, company_name 
FROM investment.flatten_chain_info
