OUTPUT_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "analysis_document",
        "test_cases",
        "generation_notes"
    ],
    "properties": {
        "analysis_document": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "title",
                "project_summary",
                "scope",
                "user_roles",
                "screens",
                "functional_requirements",
                "business_rules",
                "screen_flows",
                "open_questions",
                "qa_notes"
            ],
            "properties": {
                "title": {"type": "string"},
                "project_summary": {"type": "string"},
                "scope": {"type": "string"},
                "user_roles": {
                    "type": "array",
                    "items": {"type": "string"}
                },
                "screens": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "name",
                            "purpose",
                            "visible_elements",
                            "interactions"
                        ],
                        "properties": {
                            "name": {"type": "string"},
                            "purpose": {"type": "string"},
                            "visible_elements": {
                                "type": "array",
                                "items": {"type": "string"}
                            },
                            "interactions": {
                                "type": "array",
                                "items": {"type": "string"}
                            }
                        }
                    }
                },
                "functional_requirements": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "id",
                            "title",
                            "description",
                            "source_confidence"
                        ],
                        "properties": {
                            "id": {"type": "string"},
                            "title": {"type": "string"},
                            "description": {"type": "string"},
                            "source_confidence": {
                                "type": "string",
                                "enum": [
                                    "design_based",
                                    "assumption",
                                    "needs_confirmation"
                                ]
                            }
                        }
                    }
                },
                "business_rules": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "id",
                            "rule",
                            "source_confidence"
                        ],
                        "properties": {
                            "id": {"type": "string"},
                            "rule": {"type": "string"},
                            "source_confidence": {
                                "type": "string",
                                "enum": [
                                    "design_based",
                                    "assumption",
                                    "needs_confirmation"
                                ]
                            }
                        }
                    }
                },
                "screen_flows": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "flow_name",
                            "steps"
                        ],
                        "properties": {
                            "flow_name": {"type": "string"},
                            "steps": {
                                "type": "array",
                                "items": {"type": "string"}
                            }
                        }
                    }
                },
                "open_questions": {
                    "type": "array",
                    "items": {"type": "string"}
                },
                "qa_notes": {
                    "type": "array",
                    "items": {"type": "string"}
                }
            }
        },
        "test_cases": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "summary",
                    "test_type",
                    "priority",
                    "precondition",
                    "labels",
                    "source_confidence",
                    "steps"
                ],
                "properties": {
                    "summary": {"type": "string"},
                    "test_type": {
                        "type": "string",
                        "enum": ["Manual"]
                    },
                    "priority": {
                        "type": "string",
                        "enum": [
                            "Highest",
                            "High",
                            "Medium",
                            "Low"
                        ]
                    },
                    "precondition": {"type": "string"},
                    "labels": {
                        "type": "array",
                        "items": {"type": "string"}
                    },
                    "source_confidence": {
                        "type": "string",
                        "enum": [
                            "design_based",
                            "assumption",
                            "needs_confirmation"
                        ]
                    },
                    "steps": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": [
                                "action",
                                "data",
                                "expected_result"
                            ],
                            "properties": {
                                "action": {"type": "string"},
                                "data": {"type": "string"},
                                "expected_result": {"type": "string"}
                            }
                        }
                    }
                }
            }
        },
        "generation_notes": {
            "type": "array",
            "items": {"type": "string"}
        }
    }
}
