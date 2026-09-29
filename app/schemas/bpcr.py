
BLOOD_GROUPS = ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"]
BPCR_ANSWER_COMPONENTS = ("transport", "saved_money", "community_financial_support", "delivery_bag")


def clean_phone(v: str) -> str:
    digits = re.sub(r"\D", "", v or "")
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    if len(digits) != 10:
        raise ValueError("Phone number must be 10 digits")
    return digits


class FacilitySelectionRequest(BaseModel):
    facility_ids: list[UUID] = Field(default_factory=list, max_length=20)


class BPCRAnswersRequest(BaseModel):
    answers: dict


class BloodDonorCreate(BaseModel):
    donor_type: Literal["family", "community"]
    name: str = Field(min_length=1, max_length=100)
    blood_group: str
    relation: Optional[str] = Field(default=None, max_length=50)
    address: Optional[str] = Field(default=None, max_length=200)
    phone: str

    @field_validator("blood_group")
    @classmethod
    def _bg(cls, v: str) -> str:
        v = v.strip().upper()
        if v not in BLOOD_GROUPS:
            raise ValueError(f"blood_group must be one of {BLOOD_GROUPS}")
        return v

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        return clean_phone(v)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name is required")
        return v