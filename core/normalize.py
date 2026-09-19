"""Salesforce export -> internal schema.

The CSVs in the export bucket use Salesforce API field names, because that is
what a real opportunity export produces. This module is the only place that
knows those names. Everything downstream sees one stable internal schema.

Keeping the mapping here is the point: when the export changes shape, exactly one
file changes, and no rule, agent or prompt has to care.
"""

from typing import Any, Dict, List

# Salesforce API name -> internal field name. Custom fields carry the __c suffix.
OPPORTUNITY_MAP = {
    "Id": "deal_id", "AccountId": "account_id", "Name": "name",
    "StageName": "stage", "Amount": "amount", "CloseDate": "close_date",
    "CreatedDate": "created_date", "Owner_Name__c": "owner",
    "Original_Amount__c": "original_amount",
    "Original_Close_Date__c": "original_close_date",
    "Close_Date_Change_Count__c": "close_date_changes",
    "Stage_Entered_Date__c": "stage_entered_date",
    "Primary_Contact__c": "primary_contact_id",
}
ACCOUNT_MAP = {
    "Id": "account_id", "Name": "name", "Industry": "industry",
    "NumberOfEmployees": "employees", "Is_Existing_Customer__c": "is_existing_customer",
}
CONTACT_MAP = {
    "Id": "contact_id", "AccountId": "account_id", "Name": "name",
    "Title": "title", "Buying_Role__c": "role",
}
ACTIVITY_MAP = {
    "Id": "activity_id", "WhatId": "deal_id", "WhoId": "contact_id",
    "Type": "type", "Direction__c": "direction",
    "ActivityDate": "activity_date", "Subject": "subject",
}
NOTE_MAP = {
    "Id": "note_id", "ParentId": "deal_id", "CreatedDate": "note_date",
    "CreatedBy": "author", "Body": "body",
}
HISTORY_MAP = {
    "OpportunityId": "deal_id", "OldValue": "from_stage",
    "NewValue": "to_stage", "CreatedDate": "changed_date",
}

EXPORT_FILES = {
    "accounts": ("Account.csv", ACCOUNT_MAP),
    "contacts": ("Contact.csv", CONTACT_MAP),
    "deals": ("Opportunity.csv", OPPORTUNITY_MAP),
    "stage_history": ("OpportunityFieldHistory.csv", HISTORY_MAP),
    "activities": ("Task.csv", ACTIVITY_MAP),
    "notes": ("ContentNote.csv", NOTE_MAP),
}

_NUMERIC = {"amount", "original_amount", "close_date_changes", "employees"}


def normalize(rows: List[Dict[str, Any]], mapping: Dict[str, str]) -> List[Dict[str, Any]]:
    """Renames export columns to internal names and coerces numeric fields.

    Unmapped columns are dropped on purpose. An export carries dozens of fields
    nothing here uses, and carrying them forward invites something downstream to
    start depending on a column we never agreed to support.
    """
    out = []
    for row in rows:
        rec: Dict[str, Any] = {}
        for src, dst in mapping.items():
            if src not in row:
                continue
            val = row[src]
            if dst in _NUMERIC:
                val = float(val) if val not in (None, "") else 0
                if dst in ("close_date_changes", "employees"):
                    val = int(val)
            elif isinstance(val, str) and val.upper() in ("TRUE", "FALSE"):
                val = val.upper() == "TRUE"
            rec[dst] = val
        out.append(rec)
    return out


def denormalize(rows: List[Dict[str, Any]], mapping: Dict[str, str]) -> List[Dict[str, Any]]:
    """Internal schema -> export shape. Used to produce the synthetic export."""
    inverse = {v: k for k, v in mapping.items()}
    return [{inverse[k]: v for k, v in row.items() if k in inverse} for row in rows]
