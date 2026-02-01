"""
Intel硬件加速的 BGR→RGB 转换性能对比
测试不同实现方式的性能
"""
import cv2
import numpy as np
import time

# 生成测试帧
frame_bgr = np.random.randint(0, 256, (1080, 1920, 3), dtype=np.uint8)

def test_cvtcolor_bgr2rgb():
    """方法1: OpenCV 的 cvtColor (使用Intel IPP加速)"""
    return cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)

def test_channel_swap():
    """方法2: NumPy 通道交换 (SIMD优化)"""
    return frame_bgr[..., ::-1]  # 反向通道顺序

def test_numpy_roll():
    """方法3: NumPy roll (使用AVX2/AVX-512优化)"""
    return np.roll(frame_bgr, 1, axis=2)

def benchmark(func, name, iterations=100):
    """基准测试"""
    times = []
    for _ in range(iterations):
        start = time.perf_counter()
        result = func()
        elapsed = (time.perf_counter() - start) * 1000
        times.append(elapsed)
    
    avg = np.mean(times)
    p95 = np.percentile(times, 95)
    print(f"{name:20s}: 平均 {avg:6.3f}ms, P95 {p95:6.3f}ms, 吞吐 {1000/avg:.1f}fps")
    return avg

print("=" * 70)
print("Intel硬件加速 BGR→RGB 转换性能测试")
print(f"帧尺寸: {frame_bgr.shape}, 数据类型: {frame_bgr.dtype}")
print("=" * 70)

# OpenCV 的 cvtColor 使用 Intel IPP 加速
print("\n基于 Intel IPP 的优化:")
time1 = benchmark(test_cvtcolor_bgr2rgb, "cv2.cvtColor(COLOR_BGR2RGB)")

print("\n基于 NumPy SIMD 的优化:")
time2 = benchmark(test_channel_swap, "NumPy 通道交换 [..., ::-1]")
time3 = benchmark(test_numpy_roll, "NumPy roll()")

# 计算加速比
print("\n" + "=" * 70)
print(f"性能对比 (基准: cv2.cvtColor):")
print(f"  NumPy 通道交换:  {time1/time2:.2f}x")
print(f"  NumPy roll():   {time1/time3:.2f}x")

# 验证输出一致性
ref = test_cvtcolor_bgr2rgb()
swap_result = test_channel_swap()
roll_result = test_numpy_roll()

print("\n输出验证:")
print(f"  cvtColor vs 通道交换: {np.allclose(ref, swap_result)}")
print(f"  cvtColor vs roll:    {np.allclose(ref, roll_result)}")
print("=" * 70)
