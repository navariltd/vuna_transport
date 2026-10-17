import frappe


def execute():
	"""Map the former manual return stages without rewriting completed history."""
	if not frappe.db.table_exists("Return Request"):
		return

	frappe.db.sql(
		"""
		update `tabReturn Request` rr
		left join `tabProof of Delivery` pod on pod.name = rr.proof_of_delivery
		set rr.return_source = case
			when pod.status = 'Failed' then 'Failed Delivery'
			when pod.name is not null then 'Delivery Rejection'
			else 'Manual'
		end
		where coalesce(rr.return_source, '') = ''
		"""
	)
	frappe.db.sql(
		"""
		update `tabReturn Request`
		set notes = concat_ws('\n', nullif(notes, ''), concat('Legacy commercial resolution: ', commercial_resolution)),
			commercial_resolution = case
				when commercial_resolution = 'Credit Note' then 'Credit Customer'
				when commercial_resolution = 'No Charge' then 'No Further Action'
				when commercial_resolution = 'Re-delivery' then 'Pending'
				else commercial_resolution
			end
		where commercial_resolution in ('Credit Note', 'No Charge', 'Re-delivery')
		"""
	)
	frappe.db.sql(
		"""
		update `tabReturn Request`
		set status = case
			when coalesce(return_stock_document, '') = '' then 'Awaiting Sales Return'
			when exists (
				select 1 from `tabDelivery Note` dn
				where dn.name = `tabReturn Request`.return_stock_document and dn.docstatus = 1
			) then 'Awaiting Commercial Resolution'
			else 'Awaiting Return Submission'
		end
		where docstatus = 1
		  and status in (
			'Requested', 'Approved', 'Awaiting Return', 'In Transit',
			'Received', 'Under Inspection', 'Awaiting Resolution'
		  )
		"""
	)
	frappe.db.sql(
		"""
		update `tabReturn Request`
		set status = 'Draft'
		where docstatus = 0
		  and status not in ('Draft', 'Cancelled')
		"""
	)
	frappe.db.sql(
		"""
		update `tabReturn Request`
		set commercial_resolution = 'No Further Action',
			no_further_action = 1,
			no_further_action_remarks = coalesce(
				nullif(no_further_action_remarks, ''),
				'Preserved from the completed legacy Return Request workflow.'
			),
			resolved_by = coalesce(nullif(resolved_by, ''), owner),
			resolved_on = coalesce(resolved_on, modified)
		where status in ('Resolved', 'Closed')
		  and coalesce(credit_note, '') = ''
		  and coalesce(replacement_sales_order, '') = ''
		  and coalesce(refund_payment_entry, '') = ''
		  and coalesce(replacement_delivery_reference, '') = ''
		  and coalesce(no_further_action, 0) = 0
		"""
	)
