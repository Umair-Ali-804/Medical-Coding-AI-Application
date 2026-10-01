"""Blind held-out ICD-10-CM set (24 synthetic notes). Written BEFORE the guideline-rules work and run once at the
end, so its score is not tuned. Includes traps where a rule must NOT fire (plain hypertension, history of
cancer still under treatment, current vs former smoker, outpatient uncertain diagnoses).

Every gold code was checked against the FY2026 CDC order file (exists, billable).

    python scripts/build_blind_set.py   ->  data/evaluation/blind.jsonl
"""
import json
from pathlib import Path

CASES = [
    ("B01", "outpatient", "F", 59, """PRIMARY CARE
59 y/o woman with type 2 diabetes, A1c 9.4% today, and hypertension.
Medications: metformin 1000 mg BID, semaglutide (Ozempic) 0.5 mg weekly injection, losartan 50 mg.
Assessment:
1. Type 2 diabetes mellitus with hyperglycemia
2. Essential hypertension
Plan: Increase semaglutide to 1 mg weekly.""",
     ["E11.65", "I10", "Z79.84", "Z79.85"], []),
    ("B02", "inpatient", "M", 81, """DISCHARGE SUMMARY
81 y/o man with end stage renal disease on hemodialysis three times weekly, hypertension, and chronic diastolic
heart failure, admitted for volume overload after a missed dialysis session. Fluid removed with extra dialysis.
Discharge diagnoses:
1. Chronic diastolic heart failure
2. End stage renal disease, dependent on dialysis
3. Hypertension""",
     ["I13.2", "I50.32", "N18.6", "Z99.2"], []),
    ("B03", "outpatient", "M", 57, """CLINIC NOTE
57-year-old man for blood pressure follow-up. Home readings 120s/70s. No chest pain, no dyspnea, no edema.
Kidney function normal. Echo last year normal.
Assessment:
1. Essential hypertension, well controlled
2. Hyperlipidemia
Plan: Continue lisinopril and atorvastatin.""",
     ["I10", "E78.5"], []),
    ("B04", "outpatient", "F", 73, """ONCOLOGY FOLLOW-UP
73 y/o woman with left breast cancer diagnosed 2024, status post lumpectomy and radiation, currently on adjuvant
anastrozole which she will continue for 5 years. No evidence of recurrence.
Assessment: Malignant neoplasm of left female breast, on adjuvant aromatase inhibitor therapy.
Plan: Continue anastrozole, mammogram in 6 months.""",
     ["C50.912", "Z79.811"], []),
    ("B05", "ED", "M", 30, """EMERGENCY DEPARTMENT NOTE
30 y/o man twisted his left foot playing basketball. Lateral foot pain and swelling.
X-ray: Displaced fracture of the shaft of the left fifth metatarsal. Skin intact.
Assessment: Closed displaced fracture of left fifth metatarsal, initial encounter.
Plan: Boot, crutches, orthopedics referral.""",
     ["S92.352A"], ["W01.0XXA", "Y93.67", "X50.0XXA", "W18.40XA", "W18.49XA", "Y92.310", "Y92.39"]),
    ("B06", "outpatient", "M", 66, """PULMONARY FOLLOW-UP
66 y/o man with COPD, stable, no exacerbations this year. Former smoker, 40 pack-years, quit 5 years ago.
Assessment:
1. Chronic obstructive pulmonary disease, stable
2. Personal history of tobacco dependence
Plan: Continue tiotropium.""",
     ["J44.9", "Z87.891"], []),
    ("B07", "outpatient", "M", 52, """CLINIC NOTE
52 y/o man, current every-day cigarette smoker, about one pack per day, interested in quitting. Lipids elevated.
Assessment:
1. Nicotine dependence, cigarettes
2. Hyperlipidemia
Plan: Start varenicline, start atorvastatin.""",
     ["F17.210", "E78.5"], []),
    ("B08", "outpatient", "F", 45, """NEUROLOGY CLINIC
45 y/o woman with recurrent headaches for 2 months. Neurological exam normal.
Assessment: Headache, suspected migraine versus tension-type headache.
Plan: Headache diary, MRI brain if no improvement.""",
     ["R51.9"], []),
    ("B09", "inpatient", "F", 67, """DISCHARGE SUMMARY
67 y/o woman admitted with fever, right flank pain and pyuria. Urine culture pending at discharge.
Treated with IV ceftriaxone, transitioned to oral antibiotics.
Discharge diagnosis: Possible acute pyelonephritis.""",
     ["N10"], []),
    ("B10", "outpatient", "M", 70, """MEMORY CLINIC
70 y/o man with Alzheimer's disease with early onset. Dementia is of moderate severity, with agitation in the evenings.
Assessment: Early onset Alzheimer's disease with moderate dementia, with agitation.
Plan: Behavioral strategies, caregiver education.""",
     ["G30.0", "F02.B11"], []),
    ("B11", "outpatient", "F", 33, """PRENATAL VISIT
33 y/o G2P1 at 30 weeks gestation, routine prenatal visit. Uncomplicated pregnancy. Prior vaginal delivery.
Assessment: Supervision of normal pregnancy, third trimester, 30 weeks.
Plan: Tdap today. Glucose screen normal.""",
     ["Z34.83", "Z3A.30"], ["Z23"]),
    ("B12", "outpatient", "M", 48, """ORTHOPEDICS
48 y/o man with pain in both knees, worse with activity.
X-rays: Bilateral medial compartment narrowing.
Assessment: Bilateral primary osteoarthritis of knees.
Plan: Physical therapy, weight loss, topical NSAID.""",
     ["M17.0"], []),
    ("B13", "outpatient", "F", 61, """ORTHOPEDICS
61 y/o woman with left groin pain and stiffness. X-ray shows joint space loss of the left hip.
Assessment: Primary osteoarthritis, left hip.
Plan: PT, consider steroid injection.""",
     ["M16.12"], []),
    ("B14", "outpatient", "M", 37, """CLINIC NOTE
37 y/o man with low back pain radiating to the right buttock and posterior thigh for 10 days after lifting.
Assessment: Low back pain with right-sided sciatica.
Plan: NSAIDs, PT.""",
     ["M54.41"], []),
    ("B15", "outpatient", "F", 55, """ANNUAL PHYSICAL
55 y/o woman for routine annual physical. No complaints. BP today 148/92 and 146/90 on repeat; no prior diagnosis
of hypertension.
Assessment: General adult medical exam with abnormal findings. Elevated blood pressure reading without diagnosis of
hypertension.
Plan: Home BP log, recheck in 4 weeks.""",
     ["Z00.01", "R03.0"], []),
    ("B16", "outpatient", "M", 58, """COLONOSCOPY REPORT
Indication: Screening colonoscopy, average risk.
Findings: One 5 mm polyp in the sigmoid colon, removed with cold snare. Otherwise normal.
Impression: Screening colonoscopy with colon polyp removed.""",
     ["Z12.11", "K63.5"], []),
    ("B17", "inpatient", "M", 77, """DISCHARGE SUMMARY
77 y/o man admitted with dyspnea and edema. History of hypertension, chronic kidney disease stage 3b, and type 2
diabetes with diabetic chronic kidney disease on insulin glargine at home.
Discharge diagnoses:
1. Acute on chronic diastolic heart failure
2. Chronic kidney disease stage 3b
3. Type 2 diabetes with diabetic chronic kidney disease
4. Hypertension""",
     ["I13.0", "I50.33", "N18.32", "E11.22", "Z79.4"], []),
    ("B18", "outpatient", "F", 50, """OPHTHALMOLOGY
50 y/o woman with type 2 diabetes. Dilated exam shows mild nonproliferative diabetic retinopathy in both eyes,
no macular edema.
Assessment: Type 2 diabetes with mild nonproliferative diabetic retinopathy without macular edema, bilateral.
Plan: Repeat exam in 12 months.""",
     ["E11.3293"], []),
    ("B19", "inpatient", "M", 72, """DISCHARGE SUMMARY
72 y/o man admitted with fever, cough and dehydration. Nasal swab PCR positive for influenza B. Chest x-ray clear,
no pneumonia. Treated with oseltamivir and IV fluids.
Discharge diagnoses:
1. Influenza B with fever and cough
2. Dehydration""",
     ["J10.1", "E86.0"], []),
    ("B20", "outpatient", "M", 26, """URGENT CARE
26 y/o man cut his right index finger on broken glass. 1.5 cm laceration, nail not involved, no foreign body on
exploration.
Assessment: Laceration of right index finger without damage to nail, without foreign body, initial encounter.
Procedure: Simple repair.""",
     ["S61.210A"], ["W25.XXXA", "Y92.009"]),
    ("B21", "outpatient", "F", 69, """CARDIOLOGY
69 y/o woman with coronary artery disease of the native coronary arteries with stable angina, and a myocardial
infarction 5 years ago.
Assessment:
1. CAD of native coronary artery with stable angina
2. Old myocardial infarction
Plan: Continue beta blocker and nitrates as needed.""",
     ["I25.118", "I25.2"], []),
    ("B22", "outpatient", "M", 71, """NEUROLOGY FOLLOW-UP
71 y/o man with a history of cerebral infarction 3 years ago with full recovery and no residual deficits.
Also has essential hypertension.
Assessment:
1. Personal history of stroke without residual deficits
2. Essential hypertension""",
     ["Z86.73", "I10"], []),
    ("B23", "outpatient", "F", 40, """CLINIC NOTE
40 y/o woman with hypothyroidism on levothyroxine and heartburn. EGD showed reflux esophagitis without bleeding.
Assessment:
1. Hypothyroidism
2. Gastroesophageal reflux disease with esophagitis, without bleeding
Plan: Pantoprazole 40 mg daily.""",
     ["E03.9", "K21.00"], []),
    ("B24", "outpatient", "M", 62, """WOUND CARE
62 y/o man with type 2 diabetes on metformin, with a diabetic ulcer of the left great toe with fat layer exposed.
Assessment: Type 2 diabetes with foot ulcer, left great toe, fat layer exposed.
Plan: Debridement, offloading, culture.""",
     ["E11.621", "L97.522", "Z79.84"], []),
]


def main() -> None:
    out = Path(__file__).resolve().parents[1] / "data" / "evaluation" / "blind.jsonl"
    with out.open("w") as fh:
        for cid, enc, sex, age, note, gold, optional in CASES:
            fh.write(json.dumps({
                "document_id": cid, "clinical_text": note,
                "encounter_type": "outpatient" if enc == "ED" else enc,
                "patient_sex": sex, "patient_age": age,
                "gold_codes": [{"system": "ICD-10-CM", "code": c} for c in gold],
                "optional_codes": optional, "synthetic": True,
            }) + "\n")
    print(f"wrote {len(CASES)} cases to {out}")


if __name__ == "__main__":
    main()
