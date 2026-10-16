"""便捷运行入口：python run.py"""

from backend.app.agent.graph import build_graph


def main():
    app = build_graph()

    print("=== 普通路径 ===")
    normal = app.invoke({"latest_user_message": "头部不适两天，前额，程度轻"})
    print("最终结果：", normal["final_result"])
    print("轨迹：")
    for entry in normal["trace"]:
        print(" ", entry)

    print("\n=== 风险路径 ===")
    risk = app.invoke({"latest_user_message": "模拟人物突然呼吸困难"})
    print("最终结果：", risk["final_result"])
    print("轨迹：")
    for entry in risk["trace"]:
        print(" ", entry)


if __name__ == "__main__":
    main()
