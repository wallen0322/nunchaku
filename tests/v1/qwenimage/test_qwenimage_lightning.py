import gc
import math
import os
from pathlib import Path

import pytest
import torch
from diffusers import FlowMatchEulerDiscreteScheduler, QwenImagePipeline

from nunchaku.models.transformers.transformer_qwenimage import NunchakuQwenImageTransformer2DModel
from nunchaku.utils import get_gpu_memory, get_precision, is_turing

from ...utils import already_generate, compute_lpips
from ..utils import run_pipeline

precision = get_precision()
torch_dtype = torch.float16 if is_turing() else torch.bfloat16
dtype_str = "fp16" if torch_dtype == torch.float16 else "bf16"


model_paths = {
    "qwen-image-lightningv1.0-4steps": "nunchaku-tech/nunchaku-qwen-image/svdq-{precision}_r{rank}-qwen-image-lightningv1.0-{num_inference_steps}steps.safetensors",
    "qwen-image-lightningv1.1-8steps": "nunchaku-tech/nunchaku-qwen-image/svdq-{precision}_r{rank}-qwen-image-lightningv1.1-{num_inference_steps}steps.safetensors",
}
lora_paths = {
    "qwen-image-lightningv1.0-4steps": (
        "lightx2v/Qwen-Image-Lightning",
        "Qwen-Image-Lightning-4steps-V1.0-bf16.safetensors",
    ),
    "qwen-image-lightningv1.1-8steps": (
        "lightx2v/Qwen-Image-Lightning",
        "Qwen-Image-Lightning-8steps-V1.1-bf16.safetensors",
    ),
}


class Case:

    def __init__(self, model_name: str, num_inference_steps: int, rank: int, expected_lpips: dict[str, float]):
        self.model_name = model_name
        self.num_inference_steps = num_inference_steps
        self.rank = rank
        self.expected_lpips = expected_lpips


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            Case(
                model_name="qwen-image-lightningv1.0-4steps",
                num_inference_steps=4,
                rank=32,
                expected_lpips={"int4-bf16": 0.35, "fp4-bf16": 0.33},
            ),
            id="qwen-image-lightningv1.0-4steps-r32",
        ),
        pytest.param(
            Case(
                model_name="qwen-image-lightningv1.0-4steps",
                num_inference_steps=4,
                rank=128,
                expected_lpips={"int4-bf16": 0.32, "fp4-bf16": 0.32},
            ),
            id="qwen-image-lightningv1.0-4steps-r128",
        ),
        pytest.param(
            Case(
                model_name="qwen-image-lightningv1.1-8steps",
                num_inference_steps=8,
                rank=32,
                expected_lpips={"int4-bf16": 0.33, "fp4-bf16": 0.34},
            ),
            id="qwen-image-lightningv1.1-8steps-r32",
        ),
        pytest.param(
            Case(
                model_name="qwen-image-lightningv1.1-8steps",
                num_inference_steps=8,
                rank=128,
                expected_lpips={"int4-bf16": 0.31, "fp4-bf16": 0.32},
            ),
            id="qwen-image-lightningv1.1-8steps-r128",
        ),
    ],
)
def test_qwenimage_lightning(case: Case):
    batch_size = 1
    width = 1024
    height = 1024
    true_cfg_scale = 1.0
    rank = case.rank
    expected_lpips = case.expected_lpips[f"{precision}-{dtype_str}"]
    model_name = case.model_name
    num_inference_steps = case.num_inference_steps

    ref_root = os.environ.get("NUNCHAKU_TEST_CACHE_ROOT", os.path.join("test_results", "ref"))
    folder_name = f"w{width}h{height}t{num_inference_steps}g{true_cfg_scale}"
    save_dir_16bit = Path(ref_root) / model_name / dtype_str / folder_name

    repo_id = "Qwen/Qwen-Image"
    dataset = [
        {
            "prompt": """Bookstore window display. A sign displays “New Arrivals This Week”. Below, a shelf tag with the text “Best-Selling Novels Here”. To the side, a colorful poster advertises “Author Meet And Greet on Saturday” with a central portrait of the author. There are four books on the bookshelf, namely “The light between worlds” “When stars are scattered” “The slient patient” “The night circus” Ultra HD, 4K, cinematic composition.""",
            "filename": "bookstore",
        },
        {
            "prompt": "一副典雅庄重的对联悬挂于厅堂之中，房间是个安静古典的中式布置，桌子上放着一些青花瓷，对联上左书“义本生知人机同道善思新”，右书“通云赋智乾坤启数高志远”， 横批“智启通义”，字体飘逸，中间挂在一着一副中国风的画作，内容是岳阳楼。超清，4K，电影级构图",
            "filename": "chinese_room",
        },
        {
            "prompt": '一张企业级高质量PPT页面图像，整体采用科技感十足的星空蓝为主色调，背景融合流动的发光科技线条与微光粒子特效，营造出专业、现代且富有信任感的品牌氛围；页面顶部左侧清晰展示橘红色Alibaba标志，色彩鲜明、辨识度高。主标题位于画面中央偏上位置，使用大号加粗白色或浅蓝色字体写着“通义千问视觉基础模型”，字体现代简洁，突出技术感；主标题下方紧接一行楷体中文文字：“原生中文·复杂场景·自动布局”，字体柔和优雅，形成科技与人文的融合。下方居中排布展示了四张与图片，分别是：一幅写实与水墨风格结合的梅花特写，枝干苍劲、花瓣清雅，背景融入淡墨晕染与飘雪效果，体现坚韧不拔的精神气质；上方写着黑色的楷体"梅傲"。一株生长于山涧石缝中的兰花，叶片修长、花朵素净，搭配晨雾缭绕的自然环境，展现清逸脱俗的文人风骨；上方写着黑色的楷体"兰幽"。一组迎风而立的翠竹，竹叶随风摇曳，光影交错，背景为青灰色山岩与流水，呈现刚柔并济、虚怀若谷的文化意象；上方写着黑色的楷体"竹清"。一片盛开于秋日庭院的菊花丛，花色丰富、层次分明，配以落叶与古亭剪影，传递恬然自适的生活哲学；上方写着黑色的楷体"菊淡"。所有图片采用统一尺寸与边框样式，呈横向排列。页面底部中央用楷体小字写明“2025年8月，敬请期待”，排版工整、结构清晰，整体风格统一且细节丰富，极具视觉冲击力与品牌调性。',
            "filename": "ppt",
        },
    ]

    scheduler_config = {
        "base_image_seq_len": 256,
        "base_shift": math.log(3),  # We use shift=3 in distillation
        "invert_sigmas": False,
        "max_image_seq_len": 8192,
        "max_shift": math.log(3),  # We use shift=3 in distillation
        "num_train_timesteps": 1000,
        "shift": 1.0,
        "shift_terminal": None,  # set shift_terminal to None
        "stochastic_sampling": False,
        "time_shift_type": "exponential",
        "use_beta_sigmas": False,
        "use_dynamic_shifting": True,
        "use_exponential_sigmas": False,
        "use_karras_sigmas": False,
    }
    scheduler = FlowMatchEulerDiscreteScheduler.from_config(scheduler_config)

    if not already_generate(save_dir_16bit, len(dataset)):
        pipe = QwenImagePipeline.from_pretrained(repo_id, scheduler=scheduler, torch_dtype=torch_dtype)
        pipe.load_lora_weights(lora_paths[model_name][0], weight_name=lora_paths[model_name][1])
        pipe.fuse_lora()
        pipe.unload_lora_weights()
        pipe.enable_sequential_cpu_offload()
        run_pipeline(
            dataset=dataset,
            batch_size=1,
            pipeline=pipe,
            save_dir=save_dir_16bit,
            forward_kwargs={
                "width": width,
                "height": height,
                "num_inference_steps": num_inference_steps,
                "true_cfg_scale": true_cfg_scale,
            },
        )
        del pipe
        gc.collect()
        torch.cuda.empty_cache()

    save_dir_nunchaku = (
        Path("test_results")
        / "nunchaku"
        / model_name
        / f"{precision}_r{rank}-{dtype_str}"
        / f"{folder_name}-bs{batch_size}"
    )

    model_path = model_paths[model_name].format(precision=precision, rank=rank, num_inference_steps=num_inference_steps)
    transformer = NunchakuQwenImageTransformer2DModel.from_pretrained(model_path, torch_dtype=torch_dtype)

    pipe = QwenImagePipeline.from_pretrained(repo_id, transformer=transformer, torch_dtype=torch_dtype)

    if get_gpu_memory() > 18:
        pipe.enable_model_cpu_offload()
    else:
        transformer.set_offload(True, use_pin_memory=True, num_blocks_on_gpu=20)
        pipe._exclude_from_cpu_offload.append("transformer")
        pipe.enable_sequential_cpu_offload()

    run_pipeline(
        dataset=dataset,
        batch_size=batch_size,
        pipeline=pipe,
        save_dir=save_dir_nunchaku,
        forward_kwargs={
            "width": width,
            "height": height,
            "num_inference_steps": num_inference_steps,
            "true_cfg_scale": true_cfg_scale,
        },
    )
    del transformer
    del pipe
    gc.collect()
    torch.cuda.empty_cache()

    lpips = compute_lpips(save_dir_16bit, save_dir_nunchaku)
    print(f"lpips: {lpips}")
    assert lpips < expected_lpips * 1.10
