from pathlib import Path

from shipping_bill_agent.demo_data import write_sample_email

if __name__ == "__main__":
    output = write_sample_email(Path("data/samples/updated_bill.eml"))
    print(output)
