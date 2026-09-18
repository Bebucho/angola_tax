app_name = "angola_tax"
app_title = "Angola Tax"
app_publisher = "Metagest"
app_description = "Angola tax compliance and fiscal obligation control"
app_email = "jose.sardinha@2ms.co.ao"
app_license = "gpl-3.0"

scheduler_events = {
    "daily": [
        "angola_tax.tasks.update_fiscal_obligation_statuses"
        "angola_tax.tasks.create_fiscal_obligation_notifications",
    ]
}
