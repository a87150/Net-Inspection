import requests
import json
from datetime import datetime, timedelta
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

class AccessRecordAPI:
    def __init__(self, BASE_URL, ACCESS_KEY):
        self.access_token = ACCESS_KEY
        self.access_url = f"{BASE_URL}/api/v2/transaction/list"
        self.staff_url = f"{BASE_URL}/api/v2/person/getPersonList"

    # ===== 工具函数 =====
    @staticmethod
    def _validate_time_range(starttime, endtime, max_days=31):
        try:
            start_dt = datetime.strptime(starttime, "%Y-%m-%d %H:%M:%S")
            end_dt = datetime.strptime(endtime, "%Y-%m-%d %H:%M:%S")
        except ValueError as e:
            raise ValueError(f"时间格式错误: {e}")

        delta = end_dt - start_dt
        if delta.total_seconds() < 0:
            raise ValueError("结束时间不能早于开始时间")
        if delta > timedelta(days=max_days):
            raise ValueError(f"开始时间和结束时间跨度不得超过 {max_days} 天")

        return start_dt, end_dt

    @staticmethod
    def _save_json(data, filename):
        try:
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logging.info(f"✅ 数据已保存到文件: {filename}")
            return filename
        except Exception as e:
            logging.error(f"❌ 保存文件失败: {e}")
            return None

    def _request(self, method, url, params=None, data=None, timeout=20):
        headers = {"Content-Type": "application/json; charset=utf-8"}
        try:
            if method == "GET":
                resp = requests.get(url, headers=headers, params=params, timeout=timeout, verify=False)
            elif method == "POST":
                resp = requests.post(url, headers=headers, params=params, data=json.dumps(data), verify=False, timeout=timeout)
            else:
                logging.error(f"❌ 不支持的请求方法")
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            logging.error(f"❌ 请求失败: {e}")
        except json.JSONDecodeError as e:
            logging.error(f"❌ JSON解析失败: {e}")
        return None

    def _get_request(self, url, params):
        return self._request("GET", url, params=params)

    def _post_request(self, url, params, data={}):
        return self._request("POST", url, params=params, data=data)

    # ===== 主要功能 =====
    def get_records(self, starttime, endtime, pin=None, page_size=None, page_no=None):
        starttime, endtime = starttime.strip(), endtime.strip()
        self._validate_time_range(starttime, endtime)
        if page_size and page_size > 1000:
            logging.warning("⚠️ 每次请求记录数不超过1000条")

        params = {
            "startDate": starttime,
            "endDate": endtime,
            "personPin": pin,
            "pageSize": page_size,
            "pageNo": page_no,
            "access_token": self.access_token,
        }

        result = self._get_request(self.access_url, params)

        if not result or not result.get("data", {}).get("data"):
            logging.warning("⚠️ 未获取门禁记录信息")
            return result

        items = result["data"]["data"]

        for item in items:
            item["worksite"] = get_worksite(item.get("areaName", ""))
        logging.info("✅ 已添加场地信息到记录中")

        pins = set([i.get("pin") for i in items if i.get("pin")])

        if pins:
            logging.info(f"🔍 检测到 {len(pins)} 个人员编号，正在获取人员信息...")
            employee_info = self.get_employee_info(pins=",".join(pins))

            if employee_info:
                # logging.info(employee_info)
                emp_map = {
                    emp.get("pin"): {
                        "mobilePhone": emp.get("mobilePhone", ""),
                        "email": emp.get("email", ""),
                    }
                    for emp in employee_info
                }
                for item in items:
                    pin =item.get("pin")
                    if pin in emp_map:
                        item.update(emp_map[pin])
                logging.info("✅ 已将手机号和邮箱合并到记录中")
            else:
                logging.warning("⚠️ 获取人员信息失败，未能补充手机号和邮箱")

        return result

    def _fetch_all_pages(self, starttime, endtime, pin=None, page_size=1000):
        """自动分页获取所有记录"""
        all_data = []
        page_no = 1

        while True:
            logging.info(f"📄 正在请求第 {page_no} 页数据...")
            result = self.get_records(starttime, endtime, pin, page_size, page_no)
            if not result:
                logging.warning("⚠️ 请求失败或无数据，终止分页。")
                break

            data_list = result.get("data", {}).get("data", [])
            if not data_list:
                logging.info("✅ 所有数据已获取完毕。")
                break

            all_data.extend(data_list)
            total = result.get("data", {}).get("total", len(all_data))
            logging.info(f"✅ 第 {page_no} 页获取 {len(data_list)} 条记录，累计 {len(all_data)}/{total}")

            if len(all_data) >= total:
                break

            page_no += 1

        return all_data

    def save_records_to_file(self, data, filename=None):
        if not filename:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"access_records_{timestamp}.json"

        return self._save_json(data, filename)

    def get_records_last_day(self, pin=None, page_size=None):
        now = datetime.now()
        today_3am = now.replace(hour=3, minute=0, second=0, microsecond=0)
        yesterday_3am = today_3am - timedelta(days=1)
        starttime = yesterday_3am.strftime("%Y-%m-%d %H:%M:%S")
        endtime = today_3am.strftime("%Y-%m-%d %H:%M:%S")
        return self._fetch_all_pages(starttime, endtime, pin, page_size)

    def get_employee_info(self, pins=None, deptCodes=None,):
        params = {
            "pageNo": 1,
            "pageSize": 1000,
            "access_token": self.access_token,
        }
        if pins:
            params["pins"] = pins
        if deptCodes:
            params["deptCodes"] = deptCodes

        data = self._post_request(self.staff_url, params)
        if not data:
            logging.error("❌ 请求人员信息失败")
            return None

        employees = data.get("data", {}).get("data")
        if employees:
            logging.info(f"✅ 成功获取 {len(employees)} 条人员信息")
            return employees
        else:
            logging.warning(f"⚠️ 获取人员信息失败：{data.get('message')}")
            return None

def get_worksite(areaName):
    site_map = {"智慧谷": "ZHG", "新长海": "XCH", "企业广场": "QYGC"}
    for key, value in site_map.items():
        if key in areaName:
            return value
    return None

# ===== 使用示例 =====
if __name__ == "__main__":
    api = AccessRecordAPI(
        BASE_URL="https://10.2.251.230:8098",
        ACCESS_KEY="D753973C06089F7772F5488B157B1F3933324BD71E976CDDE9FF509BA57427B7"
    )
    starttime = "2025-10-22 00:00:01"
    endtime = "2025-10-22 23:59:59"
    page_size=1000

    logging.info(f"=== 获取 {starttime.split()[0]}-{endtime.split()[0]} 记录 ===")
    records_august = api._fetch_all_pages(starttime, endtime, page_size=page_size)
    api.save_records_to_file(records_august, f"access_records {starttime.split()[0]}-{endtime.split()[0]}.json")

    logging.info("=== 获取前一天凌晨3点到今天凌晨3点的记录 ===")
    records_last_day = api.get_records_last_day(page_size=page_size)
    api.save_records_to_file(records_last_day, "access_records_last.json")
