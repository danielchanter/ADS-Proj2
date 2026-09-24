import requests
import os

url = "https://opendata.transport.vic.gov.au/api/3/action/package_show"

data_ids = {
    "metro": "2fa2cdfa-84f1-455e-b6c9-058b92774b34", # Metropolitian Train Annual Patronage
    "regional": "2d4f81dc-f56a-4bcf-8291-ee04fe9669e6", # Regional Train Annual Patronage
    "stops and lines locations": "6d36dfd9-8693-4552-8a03-05eb29a391fd" # Public Transport Stops and Lines
}


os.makedirs("ptv_data", exist_ok=True)

for folder_name, dataset_id in data_ids.items():
    folder = f"ptv_data/{folder_name}"
    os.makedirs(folder, exist_ok=True)

    params = {"id": dataset_id}

    response = requests.get(url, params=params)
    data = response.json()

    for resource in data["result"]["resources"]:

        file_url = resource["url"]
        file_name = file_url.split("/")[-1]
        file_response = requests.get(file_url)

        if file_response.status_code == 200:
            with open(f"{folder}/{file_name}", "wb") as file:
                file.write(file_response.content)