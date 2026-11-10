"""符合度检查：程序怎么知道这一轮说的话算不算这个角色说的。

三层判定都是确定性的：
- 结构层看这段话本身成不成立；
- 身份层看自称、串场与替用户行动；
- 声线层看挂上向量后角色自己的原话是否更可能出现。
"""

from __future__ import annotations

from app import conformance, voice


def _payload(name: str = "角色D", speech: str = "") -> dict:
    """一张卡的数据段，最少要有 name。"""
    data = {"name": name}
    if speech:
        data["speech"] = speech
    return data


def test_structure_passes_on_a_well_formed_turn() -> None:
    """一段有对白、长度合适、引号配平的话，结构层通过。"""
    text = (
        "她把笔转了一圈，笑意一点点深下去。“因为你看起来，需要一个人陪着。”"
        "她压低声音，指尖在桌沿敲了两下。“今天先把题做完，渴了柜子里有水。”"
    )
    result = conformance.check_structure(text)
    assert result["verdict"] == conformance.PASS
    assert result["issues"] == []


def test_structure_fails_on_marker_and_unbalanced_quote() -> None:
    """模板标记残留与引号不配平都要被抓住。"""
    text = "她笑了一声。<|im_start|> 用户：你好。\u201c这句没有收尾"
    result = conformance.check_structure(text)
    assert result["verdict"] == conformance.FAIL
    joined = " ".join(result["issues"])
    assert "模板标记" in joined
    assert "引号" in joined


def test_structure_flags_missing_dialogue() -> None:
    """没有对白的回合在结构层就不过。"""
    text = "她站在窗边看了很久，然后转身把灯关掉，屋子里只剩一点月光，安静得能听见外面的脚步声。"
    result = conformance.check_structure(text)
    assert result["verdict"] == conformance.FAIL
    assert any("对白" in issue for issue in result["issues"])


def test_identity_flags_slipped_self_reference() -> None:
    """卡里声明自称「我」，台词里出现「朕」就是自称滑了。"""
    result = conformance.check_identity(
        "“今日？朕批的红墨未干。”",
        declared_self_reference="我",
        character_name="角色A",
    )
    assert result["verdict"] == conformance.FAIL
    assert any("自称" in issue for issue in result["issues"])


def test_identity_flags_other_character_name() -> None:
    """写到别的角色就是串场。"""
    result = conformance.check_identity(
        "“你像角色C那样/training。”她低声说。".replace("/training", ""),
        declared_self_reference="我",
        character_name="角色D",
        other_names=["角色C", "角色B"],
    )
    assert result["verdict"] == conformance.FAIL
    assert any("别的角色" in issue for issue in result["issues"])


def test_identity_flags_agent_for_the_user() -> None:
    """替用户安排动作或心理要被抓住。"""
    result = conformance.check_identity(
        "她伸手理了理你的领口。“你想好了吗。”",
        declared_self_reference="我",
        character_name="角色D",
    )
    assert result["verdict"] == conformance.FAIL
    assert any("替用户" in issue for issue in result["issues"])


def test_identity_passes_on_a_clean_turn() -> None:
    """自称与身份都对得上时通过。"""
    result = conformance.check_identity(
        "“今天先把题做完。”她把笔放下，看着你。",
        declared_self_reference="我",
        character_name="角色D",
        other_names=["角色C"],
    )
    assert result["verdict"] == conformance.PASS


def test_summarize_fails_when_any_layer_fails() -> None:
    """任何一层不过，总体就不过；问题要合起来给。"""
    layers = [
        conformance.check_structure("她笑了一声。"),
        conformance.check_identity("“朕在此。”", declared_self_reference="我"),
    ]
    summary = conformance.summarize(layers)
    assert summary["verdict"] == conformance.FAIL
    assert summary["issues"]


def test_judge_samples_split_by_spoken_line() -> None:
    """判定语料按一句台词拆开，不按整段回答。"""
    data = _payload()
    data["mes_example"] = (
        "<START>\n"
        "{{user}}: 学姐，你为什么非要帮我补习？\n"
        "{{char}}: 她把笔转了一圈。“因为你看起来，需要一个人陪着。”\n"
        "“而且，”她压低声音，“我保证你会听话。”\n"
    )
    samples = voice.voice_judge_samples(data)
    assert len(samples) == 3
    assert all(s["answer"].startswith(("因为", "而且", "我保证")) for s in samples)
    assert all(s["prompt"] == "学姐，你为什么非要帮我补习？" for s in samples)


def test_judge_samples_keep_whole_answer_without_quotes() -> None:
    """没有引号时整段算一句，别让这张卡没有样本可判。"""
    data = _payload()
    data["mes_example"] = "<START>\n{{user}}: 在吗\n{{char}}: 我在。\n"
    samples = voice.voice_judge_samples(data)
    assert len(samples) == 1
    assert samples[0]["answer"] == "我在。"


def test_card_persona_joins_the_three_layers() -> None:
    """声线描述由设定三层拼成。"""
    data = {
        "name": "角色D",
        "identity": "温柔的学姐",
        "behavior": "耐心",
        "speech": "声音轻",
    }
    persona = voice.card_persona(data)
    assert "温柔的学姐" in persona
    assert "耐心" in persona
    assert "声音轻" in persona
